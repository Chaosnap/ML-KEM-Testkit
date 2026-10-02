// mlkem_poly_alu.sv - Pipelined polynomial arithmetic engine for ML-KEM (q = 3329)
//
// Operates in place on the polynomial RAM (both ports, read latency 2:
// mlkem_polyram has output registers). Each RAM word holds two
// coefficients {f[2w+1], f[2w]}. Two Barrett multipliers (mlkem_modmul,
// latency 7, one DSP each) are kept busy by a fixed schedule. Every RAM
// port command (enable, write enable, address, data) is decided one cycle
// ahead and registered, and every pipeline stage does at most one modular
// add or subtract, for timing above 200 MHz.
//
// T is the cycle a word's (or word pair's) read address is at the RAM;
// its data is available at T+2. Writes go on odd cycles (relative to the
// pass), reads of a new word or pair on even cycles:
//
//   OP_NTT / OP_INTT (FIPS 203 Algorithms 9 / 10), word pair every 2 cycles
//     T read jw, jb   T+4 zeta * b (NTT) or zeta * (b - a) (INTT)
//     T+13 butterfly  T+15 write jw, jb        -> one butterfly per cycle
//     INTT ends with a pass multiplying by 128^-1 = 3303: one word per cycle,
//     read on port A at T, write on port B at T+15.
//   OP_BMUL (Algorithms 11 / 12), C = [C +] A (x) B, one word every 2 cycles
//     T read A, B   T+1 read C (accumulate)   T+3 a0*b0, a1*b1
//     T+4 (a0+a1)(b0+b1)   T+10 (a1*b1)*gamma   T+21 write C (port B)
//   OP_ADD / OP_SUB: C = A +/- B, one word every 2 cycles, write at T+5.
//
// Between NTT / INTT layers the pipeline drains. Slot C of BMUL must differ
// from A and B (gen_mlkem_ucode.py asserts this). Results are canonical.

module mlkem_poly_alu (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,            // Synchronous abort.

    input  logic        start,
    input  logic [2:0]  op,
    input  logic [2:0]  slot_a,
    input  logic [2:0]  slot_b,
    input  logic [2:0]  slot_c,
    input  logic        acc,            // OP_BMUL: accumulate into slot C.
    output logic        done,           // One-cycle pulse.

    // Polynomial RAM ports (all outputs registered).
    output logic        ra_en,
    output logic        ra_we,
    output logic [9:0]  ra_addr,
    output logic [23:0] ra_din,
    input  logic [23:0] ra_dout,
    output logic        rb_en,
    output logic        rb_we,
    output logic [9:0]  rb_addr,
    output logic [23:0] rb_din,
    input  logic [23:0] rb_dout
);

    // Registered active-high synchronous reset, local to this module
    // (replicated by fanout; no inverter on the high-fanout net).
    (* max_fanout = 32 *) logic srst;
    always_ff @(posedge clk)
        srst <= !rst_n;

    localparam logic [2:0] OP_NTT  = 3'd0;
    localparam logic [2:0] OP_INTT = 3'd1;
    localparam logic [2:0] OP_BMUL = 3'd2;
    localparam logic [2:0] OP_ADD  = 3'd3;
    localparam logic [2:0] OP_SUB  = 3'd4;

    localparam logic [11:0] Q      = 12'd3329;
    localparam logic [11:0] N_INV  = 12'd3303;  // 128^-1 mod q.
    localparam int          PD     = 21;        // Issue-pipeline depth (BMUL write at T+21).

    // ---------------------------------------------------------------------
    // Modular add / sub helpers (inputs canonical).
    // ---------------------------------------------------------------------
    // Both candidates are computed in parallel (the second as a carry-save
    // 3-input add: one LUT level plus one carry chain) and one bit selects,
    // instead of add -> compare -> subtract in series. Arithmetic mod 2^13:
    //   madd: s = x + y,  t = x + y - q;   x + y >= q  <=>  t[12] == 0
    //   msub: d = x - y,  e = x - y + q;   x <  y      <=>  d[12] == 1
    function automatic logic [12:0] add3(input logic [12:0] a, input logic [12:0] b,
                                         input logic [12:0] c);
        logic [12:0] sm, cy;
        sm = a ^ b ^ c;
        cy = (a & b) | (a & c) | (b & c);
        return sm + {cy[11:0], 1'b0};
    endfunction

    function automatic logic [11:0] madd(input logic [11:0] x, input logic [11:0] y);
        logic [12:0] s, t;
        s = {1'b0, x} + {1'b0, y};
        t = add3({1'b0, x}, {1'b0, y}, 13'd4863);        // 2^13 - q
        return t[12] ? s[11:0] : t[11:0];
    endfunction

    function automatic logic [11:0] msub(input logic [11:0] x, input logic [11:0] y);
        logic [12:0] d, e;
        d = {1'b0, x} - {1'b0, y};
        e = add3({1'b0, x}, ~{1'b0, y}, 13'd3330);       // x - y + q
        return d[12] ? e[11:0] : d[11:0];
    endfunction

    function automatic logic [23:0] madd2(input logic [23:0] x, input logic [23:0] y);
        return {madd(x[23:12], y[23:12]), madd(x[11:0], y[11:0])};
    endfunction

    function automatic logic [23:0] msub2(input logic [23:0] x, input logic [23:0] y);
        return {msub(x[23:12], y[23:12]), msub(x[11:0], y[11:0])};
    endfunction

    // ---------------------------------------------------------------------
    // Pass control. `issue` is decided in cycle T-1 for a read at T.
    // ---------------------------------------------------------------------
    logic        running;
    logic [2:0]  cur_op;
    logic        is_ntt, is_intt, is_bmul, is_add;   // Decoded cur_op (registered).
    logic        scale;         // INTT final pass (x 128^-1).
    logic        butterfly;     // NTT / INTT layer pass (registered).
    logic [2:0]  sa, sb, sc;
    logic        acc_r;
    logic [2:0]  layer;         // NTT/INTT layer 0..6.
    logic [7:0]  item;          // Next word pair (0..64) or word (0..128).
    logic        last_item;     // item == number of items (registered).
    logic        ph;            // 2-cycle slot phase; issue when 0.
    logic [4:0]  outst;         // Issued words whose write is not yet scheduled.

    logic        issue, wr_ev;
    assign issue = running && !last_item && (scale || !ph);

    // Butterfly word addressing for layer `layer`.
    //   NTT : lenw = 64 >> layer (len = 128 >> layer coefficients)
    //   INTT: lenw = 1  << layer (len = 2   << layer coefficients)
    logic [2:0]  lg_lenw;
    logic [6:0]  lenw, jw, jb, blk, it7;
    logic [6:0]  zeta_idx;
    logic [6:0]  zi;            // Zeta index registered (during T).
    logic [11:0] zeta_val, gamma_val;
    always_comb begin
        it7     = item[6:0];
        lg_lenw = is_ntt ? 3'(6 - layer) : layer;
        lenw    = 7'd1 << lg_lenw;
        blk     = it7 >> lg_lenw;
        jw      = ((it7 >> lg_lenw) << (lg_lenw + 3'd1)) | (it7 & (lenw - 7'd1));
        jb      = jw + lenw;
        // NTT:  zeta index = 2^layer + blk        (k increments from 1)
        // INTT: zeta index = 2^(7-layer) - 1 - blk (k decrements from 127)
        if (is_ntt)
            zeta_idx = (7'd1 << layer) + blk;
        else
            zeta_idx = 7'((8'd1 << (3'd7 - layer)) - 8'd1 - {1'b0, blk});
        if (!butterfly) begin
            jw = it7;
            jb = it7;
        end
    end

    mlkem_zetas u_zetas (
        .zeta_idx  (zi),
        .zeta      (zeta_val),
        .gamma_idx (it7),
        .gamma     (gamma_val)
    );

    // ---------------------------------------------------------------------
    // Issue pipeline: pv[i] is high during cycle T+i of a word read at T;
    // word addresses and constants travel with it.
    // ---------------------------------------------------------------------
    logic [PD-1:0] pv;
    logic [6:0]    pjw [0:PD-1];
    logic [6:0]    pjb [0:PD-1];
    (* rom_style = "distributed" *) logic [11:0] pz [0:2];    // zeta, pz[2] during T+3
    (* rom_style = "distributed" *) logic [11:0] pg [0:10];   // gamma, pg[10] during T+10

    always_ff @(posedge clk) begin
        pjw[0] <= jw;
        pjb[0] <= jb;
        for (int i = 1; i < PD; i++) begin
            pjw[i] <= pjw[i - 1];
            pjb[i] <= pjb[i - 1];
        end
        zi    <= zeta_idx;
        pz[0] <= zeta_val;                      // ROM read from zi (registered index)
        for (int i = 1; i < 3; i++)
            pz[i] <= pz[i - 1];
        pg[0] <= gamma_val;
        for (int i = 1; i < 11; i++)
            pg[i] <= pg[i - 1];
    end

    // Write scheduled in the next cycle (port command registered now).
    always_comb begin
        if (is_bmul)     wr_ev = pv[20];        // write T+21
        else if (is_add) wr_ev = pv[4];         // write T+5 (ADD and SUB)
        else             wr_ev = pv[14];        // write T+15
    end

    // ---------------------------------------------------------------------
    // Datapath.
    // ---------------------------------------------------------------------
    logic [23:0] la, lb;            // T+3: RAM words (plain registers).
    logic [23:0] la2, lb2;          // T+4: the same, one stage later.
    logic [23:0] ls, ld;            // T+4: a +/- b, b - a.
    logic [11:0] lz;                // T+4: zeta.
    logic [11:0] ksa, ksb;          // T+4: a0+a1, b0+b1.
    logic [23:0] mr, mr2;           // T+12, T+13: multiplier outputs.
    logic [23:0] pd  [0:8];         // la2 / ls, pd[8] during T+13.
    logic [23:0] wa_ntt, wb_ntt;    // T+14: NTT butterfly.
    logic [23:0] wa_intt, wb_mr;    // T+14: INTT a + b, products (INTT, scaling).
    logic [23:0] wb, wbd;           // T+19, T+20: BMUL result.
    logic [11:0] p00, p11;          // T+11: a0*b0, a1*b1.
    logic [11:0] p11d;              // T+12: a1*b1.
    logic [11:0] p00d [0:5];        // a0*b0, p00d[5] during T+17.
    logic [11:0] s1;                // T+12: (a0+a1)(b0+b1) - a0*b0.
    logic [11:0] h1;                // T+13: ... - a1*b1.
    logic [11:0] h1d [0:4];         // h1d[4] during T+18.
    logic [11:0] h0;                // T+18: a0*b0 + a1*b1*gamma.
    logic [23:0] cin;               // T+4: C word.
    logic [23:0] cd  [0:13];        // cd[13] during T+18.
    logic        sub_r;             // OP_SUB (registered).

    logic [11:0] m0_a, m0_b, m0_r;
    logic [11:0] m1_a, m1_b, m1_r;

    mlkem_modmul u_mul0 (.clk(clk), .a(m0_a), .b(m0_b), .r(m0_r));
    mlkem_modmul u_mul1 (.clk(clk), .a(m1_a), .b(m1_b), .r(m1_r));

    // Multiplier operands (registered sources only).
    always_comb begin
        if (is_bmul) begin
            if (pv[4]) begin                    // T+4: (a0+a1)*(b0+b1)
                m0_a = ksa;       m0_b = ksb;
            end else begin                      // T+3: a0*b0
                m0_a = la[11:0];  m0_b = lb[11:0];
            end
            if (pv[10]) begin                   // T+10: (a1*b1)*gamma
                m1_a = m1_r;      m1_b = pg[10];
            end else begin                      // T+3: a1*b1
                m1_a = la[23:12]; m1_b = lb[23:12];
            end
        end else if (scale) begin               // T+4
            m0_a = la2[11:0];  m0_b = N_INV;
            m1_a = la2[23:12]; m1_b = N_INV;
        end else if (is_intt) begin             // T+4: zeta * (b - a)
            m0_a = ld[11:0];   m0_b = lz;
            m1_a = ld[23:12];  m1_b = lz;
        end else begin                          // T+4, NTT: zeta * b
            m0_a = lb2[11:0];  m0_b = lz;
            m1_a = lb2[23:12]; m1_b = lz;
        end
    end

    always_ff @(posedge clk) begin
        // T+2 -> T+3: RAM outputs into plain registers.
        la  <= ra_dout;
        lb  <= rb_dout;
        // T+3 -> T+4.
        la2 <= la;
        lb2 <= lb;
        ls  <= sub_r ? msub2(la, lb) : madd2(la, lb);
        ld  <= msub2(lb, la);
        lz  <= pz[2];
        ksa <= madd(la[11:0], la[23:12]);
        ksb <= madd(lb[11:0], lb[23:12]);
        cin <= acc_r ? ra_dout : 24'd0;         // C word read at T+1
        // First butterfly operand, T+4 -> T+13.
        pd[0] <= is_intt ? ls : la2;
        for (int i = 1; i < 9; i++)
            pd[i] <= pd[i - 1];
        // Multiplier outputs (T+11 for products issued at T+4).
        mr  <= {m1_r, m0_r};
        mr2 <= mr;
        p00 <= m0_r;
        p11 <= m1_r;
        // Butterfly results (T+13), one variant per register.
        wa_ntt  <= madd2(pd[8], mr2);
        wb_ntt  <= msub2(pd[8], mr2);
        wa_intt <= pd[8];
        wb_mr   <= mr2;
        // BMUL: h1 = (a0+a1)(b0+b1) - a0*b0 - a1*b1, one subtraction per stage.
        s1   <= msub(m0_r, p00);                // T+11 (m0_r = Karatsuba product)
        p11d <= p11;
        h1   <= msub(s1, p11d);                 // T+12
        p00d[0] <= p00;
        for (int i = 1; i < 6; i++)
            p00d[i] <= p00d[i - 1];
        h1d[0] <= h1;
        for (int i = 1; i < 5; i++)
            h1d[i] <= h1d[i - 1];
        h0 <= madd(p00d[5], m1_r);              // T+17 (m1_r = gamma product)
        cd[0] <= cin;
        for (int i = 1; i < 14; i++)
            cd[i] <= cd[i - 1];
        wb  <= {madd(cd[13][23:12], h1d[4]), madd(cd[13][11:0], h0)};   // T+18
        wbd <= wb;                              // T+19
    end

    // ---------------------------------------------------------------------
    // Registered RAM port commands for the next cycle.
    // ---------------------------------------------------------------------
    always_ff @(posedge clk) begin
        ra_en <= 1'b0; ra_we <= 1'b0;
        rb_en <= 1'b0; rb_we <= 1'b0;
        ra_din <= is_intt ? wa_intt : wa_ntt;
        rb_din <= is_add ? ls : is_bmul ? wbd : (scale || is_intt) ? wb_mr : wb_ntt;
        if (issue) begin                        // Reads at T.
            ra_en   <= 1'b1;
            ra_addr <= {sa, jw};
            if (!scale) begin
                rb_en   <= 1'b1;
                rb_addr <= butterfly ? {sa, jb} : {sb, it7};
            end
        end
        if (is_bmul && acc_r && pv[0]) begin     // C read at T+1.
            ra_en   <= 1'b1;
            ra_addr <= {sc, pjw[0]};
        end
        if (wr_ev) begin
            if (butterfly) begin
                ra_en <= 1'b1; ra_we <= 1'b1; ra_addr <= {sa, pjw[14]};
                rb_en <= 1'b1; rb_we <= 1'b1; rb_addr <= {sa, pjb[14]};
            end else begin
                rb_en <= 1'b1; rb_we <= 1'b1;
                if (scale)
                    rb_addr <= {sa, pjw[14]};
                else if (is_bmul)
                    rb_addr <= {sc, pjw[20]};
                else
                    rb_addr <= {sc, pjw[4]};
            end
        end
    end

    // ---------------------------------------------------------------------
    // Sequencing.
    // ---------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (srst || clr) begin
            running   <= 1'b0;
            done      <= 1'b0;
            outst     <= '0;
            pv        <= '0;
            ph        <= 1'b0;
            last_item <= 1'b0;
            if (srst) begin
                cur_op  <= OP_NTT;
                is_ntt  <= 1'b1;
                is_intt <= 1'b0;
                is_bmul <= 1'b0;
                is_add  <= 1'b0;
                sub_r   <= 1'b0;
                scale   <= 1'b0;
                butterfly <= 1'b1;
                sa <= '0; sb <= '0; sc <= '0;
                acc_r   <= 1'b0;
                layer   <= '0;
                item    <= '0;
            end
        end else begin
            done  <= 1'b0;
            pv    <= {pv[PD-2:0], issue};
            outst <= outst + 5'(issue) - 5'(wr_ev);
            if (running) begin
                ph <= !ph;
                if (issue) begin
                    item      <= item + 8'd1;
                    last_item <= butterfly ? (item == 8'd63) : (item == 8'd127);
                end
                // Pass complete: everything issued and every write scheduled.
                if (last_item && outst == 5'(wr_ev) && !issue) begin
                    item      <= '0;
                    last_item <= 1'b0;
                    ph        <= 1'b0;
                    if (butterfly && layer != 3'd6) begin
                        layer <= layer + 3'd1;
                    end else if (butterfly && is_intt) begin
                        scale     <= 1'b1;
                        butterfly <= 1'b0;
                    end else begin
                        running <= 1'b0;
                        done    <= 1'b1;
                    end
                end
            end else if (start) begin
                pv        <= '0;    // Drop tail bits of the previous operation.
                running   <= 1'b1;
                cur_op    <= op;
                is_ntt    <= (op == OP_NTT);
                is_intt   <= (op == OP_INTT);
                is_bmul   <= (op == OP_BMUL);
                is_add    <= (op == OP_ADD || op == OP_SUB);
                sub_r     <= (op == OP_SUB);
                scale     <= 1'b0;
                butterfly <= (op == OP_NTT || op == OP_INTT);
                sa        <= slot_a;
                sb        <= slot_b;
                sc        <= slot_c;
                acc_r     <= acc;
                layer     <= '0;
                item      <= '0;
                ph        <= 1'b0;
                last_item <= 1'b0;
            end
        end
    end

endmodule
