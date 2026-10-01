// mlkem_poly_alu.sv - Pipelined polynomial arithmetic engine for ML-KEM (q = 3329)
//
// Operates in place on the polynomial RAM (both ports). Each RAM word
// holds two coefficients {f[2w+1], f[2w]}. Two pipelined Barrett
// multipliers (mlkem_modmul, latency ML = 5, one DSP each) are kept busy
// by a fixed schedule; T is the cycle a word (pair) is issued:
//
//   OP_NTT : slot A = NTT(slot A)                 FIPS 203 Algorithm 9
//   OP_INTT: slot A = NTT^-1(slot A)               FIPS 203 Algorithm 10
//            (7 Gentleman-Sande layers + a pass multiplying by 3303 = 128^-1)
//            Word pair (jw, jb) every 2 cycles: reads at T (even), lanes
//            through the multipliers at T+2, writes at T+9 (odd), i.e. one
//            butterfly per cycle. The scaling pass streams one word per
//            cycle (read on port A, write on port B).
//   OP_BMUL: slot C = [slot C +] slot A (x) slot B FIPS 203 Algorithms 11/12
//            One word every 2 cycles, 4 multiplies (Karatsuba):
//              T+2  a0*b0, a1*b1        T+3  (a0+a1)*(b0+b1)
//              T+7  (a1*b1)*gamma       T+13 write C (odd cycle, port B)
//            C is read at T+1 (odd cycle, port A) when accumulating.
//   OP_ADD : slot C = slot A + slot B   one word every 2 cycles, write T+3
//   OP_SUB : slot C = slot A - slot B
//
// Between NTT / INTT layers the pipeline drains (all writes of a layer land
// before the next layer reads). Slot C of BMUL must differ from A and B
// (gen_mlkem_ucode.py asserts this). All results are canonical (0 <= x < q).

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

    // Polynomial RAM ports.
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

    localparam logic [2:0] OP_NTT  = 3'd0;
    localparam logic [2:0] OP_INTT = 3'd1;
    localparam logic [2:0] OP_BMUL = 3'd2;
    localparam logic [2:0] OP_ADD  = 3'd3;
    localparam logic [2:0] OP_SUB  = 3'd4;

    localparam logic [11:0] Q      = 12'd3329;
    localparam logic [11:0] N_INV  = 12'd3303;  // 128^-1 mod q.
    localparam int          PD     = 13;        // Issue-pipeline depth (BMUL write tap).

    // ---------------------------------------------------------------------
    // Modular add / sub helpers (inputs canonical).
    // ---------------------------------------------------------------------
    function automatic logic [11:0] madd(input logic [11:0] x, input logic [11:0] y);
        logic [12:0] s;
        s = {1'b0, x} + {1'b0, y};
        return (s >= {1'b0, Q}) ? 12'(s - {1'b0, Q}) : s[11:0];
    endfunction

    function automatic logic [11:0] msub(input logic [11:0] x, input logic [11:0] y);
        logic [12:0] d;
        d = {1'b0, x} - {1'b0, y};
        return d[12] ? 12'(d + {1'b0, Q}) : d[11:0];
    endfunction

    function automatic logic [23:0] madd2(input logic [23:0] x, input logic [23:0] y);
        return {madd(x[23:12], y[23:12]), madd(x[11:0], y[11:0])};
    endfunction

    function automatic logic [23:0] msub2(input logic [23:0] x, input logic [23:0] y);
        return {msub(x[23:12], y[23:12]), msub(x[11:0], y[11:0])};
    endfunction

    // ---------------------------------------------------------------------
    // Pass control.
    // ---------------------------------------------------------------------
    logic        running;
    logic [2:0]  cur_op;
    logic        scale;         // INTT final pass (x 128^-1).
    logic [2:0]  sa, sb, sc;
    logic        acc_r;
    logic [2:0]  layer;         // NTT/INTT layer 0..6.
    logic [7:0]  item;          // Next word pair (0..64) or word (0..128).
    logic        ph;            // 2-cycle slot phase; issue when 0.
    logic [4:0]  outst;         // Issued words whose write is pending.

    logic        butterfly, two_cycle, last_item, issue, wr_ev;
    assign butterfly = (cur_op == OP_NTT || cur_op == OP_INTT) && !scale;
    assign two_cycle = !scale;
    assign last_item = butterfly ? (item == 8'd64) : (item == 8'd128);
    assign issue     = running && !last_item && (!two_cycle || !ph);

    // Butterfly word addressing for layer `layer`.
    //   NTT : lenw = 64 >> layer (len = 128 >> layer coefficients)
    //   INTT: lenw = 1  << layer (len = 2   << layer coefficients)
    logic [2:0]  lg_lenw;
    logic [6:0]  lenw, jw, jb, blk, it7;
    logic [6:0]  zeta_idx;
    logic [11:0] zeta_val, gamma_val;
    always_comb begin
        it7     = item[6:0];
        lg_lenw = (cur_op == OP_NTT) ? 3'(6 - layer) : layer;
        lenw    = 7'd1 << lg_lenw;
        blk     = it7 >> lg_lenw;
        jw      = ((it7 >> lg_lenw) << (lg_lenw + 3'd1)) | (it7 & (lenw - 7'd1));
        jb      = jw + lenw;
        // NTT:  zeta index = 2^layer + blk        (k increments from 1)
        // INTT: zeta index = 2^(7-layer) - 1 - blk (k decrements from 127)
        if (cur_op == OP_NTT)
            zeta_idx = (7'd1 << layer) + blk;
        else
            zeta_idx = 7'((8'd1 << (3'd7 - layer)) - 8'd1 - {1'b0, blk});
        if (!butterfly) begin
            jw = it7;
            jb = it7;
        end
    end

    mlkem_zetas u_zetas (
        .zeta_idx  (zeta_idx),
        .zeta      (zeta_val),
        .gamma_idx (it7),
        .gamma     (gamma_val)
    );

    // ---------------------------------------------------------------------
    // Issue pipeline: valid bit and word addresses, one stage per cycle.
    // pv[i] is high during cycle T+1+i of a word issued at T.
    // ---------------------------------------------------------------------
    logic [PD-1:0] pv;
    logic [6:0]    pjw [0:PD-1];
    logic [6:0]    pjb [0:PD-1];
    (* rom_style = "distributed" *) logic [11:0] z0;    // zeta of the word issued last cycle
    (* rom_style = "distributed" *) logic [11:0] pg [0:6];  // gamma, pg[6] during T+7

    always_ff @(posedge clk) begin
        pjw[0] <= jw;
        pjb[0] <= jb;
        for (int i = 1; i < PD; i++) begin
            pjw[i] <= pjw[i - 1];
            pjb[i] <= pjb[i - 1];
        end
        z0    <= zeta_val;
        pg[0] <= gamma_val;
        for (int i = 1; i < 7; i++)
            pg[i] <= pg[i - 1];
    end

    // Write tap per operation.
    always_comb begin
        if (cur_op == OP_BMUL)                          wr_ev = pv[12];
        else if (cur_op == OP_ADD || cur_op == OP_SUB)  wr_ev = pv[2];
        else                                            wr_ev = pv[8];
    end

    // ---------------------------------------------------------------------
    // Datapath.
    // ---------------------------------------------------------------------
    logic [23:0] la, lb, ls, ld;    // T+2: operands, a+b / a-b, b-a.
    logic [11:0] lz;                // T+2: zeta.
    logic [11:0] ksa, ksb;          // T+3: a0+a1, b0+b1.
    logic [23:0] mr;                // T+8: multiplier outputs.
    logic [23:0] pd  [0:5];         // la / ls delayed to T+8.
    logic [11:0] p00 [0:4];         // a0*b0, p00[k] during T+8+k.
    logic [11:0] p11;               // a1*b1 during T+8.
    logic [11:0] h1  [0:3];         // a0*b1 + a1*b0, h1[3] during T+12.
    logic [23:0] cin;               // C word read at T+1, during T+3.
    logic [23:0] cd  [0:8];         // C delayed, cd[8] during T+12.
    logic [23:0] wa, wb;            // Write data.

    logic [11:0] m0_a, m0_b, m0_r;
    logic [11:0] m1_a, m1_b, m1_r;

    mlkem_modmul u_mul0 (.clk(clk), .a(m0_a), .b(m0_b), .r(m0_r));
    mlkem_modmul u_mul1 (.clk(clk), .a(m1_a), .b(m1_b), .r(m1_r));

    // Multiplier operands.
    always_comb begin
        m0_a = '0; m0_b = '0; m1_a = '0; m1_b = '0;
        if (cur_op == OP_BMUL) begin
            if (pv[1]) begin                    // T+2: a0*b0, a1*b1
                m0_a = la[11:0];  m0_b = lb[11:0];
                m1_a = la[23:12]; m1_b = lb[23:12];
            end
            if (pv[2]) begin                    // T+3: (a0+a1)*(b0+b1)
                m0_a = ksa; m0_b = ksb;
            end
            if (pv[6]) begin                    // T+7: (a1*b1)*gamma
                m1_a = m1_r; m1_b = pg[6];
            end
        end else if (scale) begin
            m0_a = la[11:0];  m0_b = N_INV;
            m1_a = la[23:12]; m1_b = N_INV;
        end else if (cur_op == OP_INTT) begin   // zeta * (b - a)
            m0_a = ld[11:0];  m0_b = lz;
            m1_a = ld[23:12]; m1_b = lz;
        end else begin                          // NTT: zeta * b
            m0_a = lb[11:0];  m0_b = lz;
            m1_a = lb[23:12]; m1_b = lz;
        end
    end

    always_ff @(posedge clk) begin
        // T+1 -> T+2: latch RAM outputs.
        la <= ra_dout;
        lb <= rb_dout;
        ls <= (cur_op == OP_SUB) ? msub2(ra_dout, rb_dout) : madd2(ra_dout, rb_dout);
        ld <= msub2(rb_dout, ra_dout);
        lz <= z0;
        // T+2 -> T+3: Karatsuba sums; C word (read at T+1) valid at T+2.
        ksa <= madd(la[11:0], la[23:12]);
        ksb <= madd(lb[11:0], lb[23:12]);
        cin <= acc_r ? ra_dout : 24'd0;
        // Butterfly second operand delayed T+2 -> T+8.
        pd[0] <= (cur_op == OP_INTT) ? ls : la;
        for (int i = 1; i < 6; i++)
            pd[i] <= pd[i - 1];
        // T+7 -> T+8: multiplier outputs.
        mr     <= {m1_r, m0_r};
        p00[0] <= m0_r;
        p11    <= m1_r;
        for (int i = 1; i < 5; i++)
            p00[i] <= p00[i - 1];
        // T+8: h1 = (a0+a1)(b0+b1) - a0b0 - a1b1 (Karatsuba product in m0_r).
        h1[0] <= msub(msub(m0_r, p00[0]), p11);
        for (int i = 1; i < 4; i++)
            h1[i] <= h1[i - 1];
        // C delayed from T+3 to T+12.
        cd[0] <= cin;
        for (int i = 1; i < 9; i++)
            cd[i] <= cd[i - 1];

        // Write data, registered one cycle before the write.
        case (cur_op)
            OP_BMUL: begin                      // T+12, gamma product in m1_r
                wa <= '0;
                wb <= {madd(cd[8][23:12], h1[3]),
                       madd(cd[8][11:0], madd(p00[4], m1_r))};
            end
            OP_ADD, OP_SUB: begin               // T+2
                wa <= '0;
                wb <= ls;
            end
            default: begin                      // T+8
                if (scale) begin
                    wa <= '0;
                    wb <= mr;
                end else if (cur_op == OP_INTT) begin
                    wa <= pd[5];
                    wb <= mr;
                end else begin
                    wa <= madd2(pd[5], mr);
                    wb <= msub2(pd[5], mr);
                end
            end
        endcase
    end

    // ---------------------------------------------------------------------
    // RAM ports.
    // ---------------------------------------------------------------------
    always_comb begin
        ra_en = 1'b0; ra_we = 1'b0; ra_addr = '0; ra_din = wa;
        rb_en = 1'b0; rb_we = 1'b0; rb_addr = '0; rb_din = wb;
        if (issue) begin                        // Reads at T.
            ra_en   = 1'b1;
            ra_addr = {sa, jw};
            if (!scale) begin
                rb_en   = 1'b1;
                rb_addr = butterfly ? {sa, jb} : {sb, it7};
            end
        end
        if (cur_op == OP_BMUL && acc_r && pv[0]) begin   // C read at T+1.
            ra_en   = 1'b1;
            ra_addr = {sc, pjw[0]};
        end
        if (wr_ev) begin
            if (butterfly) begin
                ra_en = 1'b1; ra_we = 1'b1; ra_addr = {sa, pjw[8]};
                rb_en = 1'b1; rb_we = 1'b1; rb_addr = {sa, pjb[8]};
            end else begin
                rb_en = 1'b1; rb_we = 1'b1;
                if (scale)
                    rb_addr = {sa, pjw[8]};
                else if (cur_op == OP_BMUL)
                    rb_addr = {sc, pjw[12]};
                else
                    rb_addr = {sc, pjw[2]};
            end
        end
    end

    // ---------------------------------------------------------------------
    // Sequencing.
    // ---------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            running <= 1'b0;
            done    <= 1'b0;
            cur_op  <= OP_NTT;
            scale   <= 1'b0;
            sa <= '0; sb <= '0; sc <= '0;
            acc_r   <= 1'b0;
            layer   <= '0;
            item    <= '0;
            ph      <= 1'b0;
            outst   <= '0;
            pv      <= '0;
        end else if (clr) begin
            running <= 1'b0;
            done    <= 1'b0;
            outst   <= '0;
            pv      <= '0;
        end else begin
            done  <= 1'b0;
            pv    <= {pv[PD-2:0], issue};
            outst <= outst + 5'(issue) - 5'(wr_ev);
            if (running) begin
                ph <= !ph;
                if (issue)
                    item <= item + 8'd1;
                // Pass complete: everything issued and written.
                if (last_item && outst == 5'(wr_ev) && !issue) begin
                    item <= '0;
                    ph   <= 1'b0;
                    if (butterfly && layer != 3'd6) begin
                        layer <= layer + 3'd1;
                    end else if (butterfly && cur_op == OP_INTT) begin
                        scale <= 1'b1;
                    end else begin
                        running <= 1'b0;
                        done    <= 1'b1;
                    end
                end
            end else if (start) begin
                pv      <= '0;      // Drop tail bits of the previous operation.
                running <= 1'b1;
                cur_op  <= op;
                scale   <= 1'b0;
                sa      <= slot_a;
                sb      <= slot_b;
                sc      <= slot_c;
                acc_r   <= acc;
                layer   <= '0;
                item    <= '0;
                ph      <= 1'b0;
            end
        end
    end

endmodule
