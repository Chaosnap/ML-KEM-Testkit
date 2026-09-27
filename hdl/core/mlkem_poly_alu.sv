// mlkem_poly_alu.sv - Polynomial arithmetic engine for ML-KEM (q = 3329)
//
// Operates in place on the polynomial RAM (both ports). Each RAM word
// holds two coefficients {f[2w+1], f[2w]}, so every step processes two
// coefficients with two pipelined Barrett multipliers.
//
// Operations (op):
//   OP_NTT : slot A = NTT(slot A)                 FIPS 203 Algorithm 9
//   OP_INTT: slot A = NTT^-1(slot A)               FIPS 203 Algorithm 10
//            (7 Gentleman-Sande layers + multiply by 3303 = 128^-1 mod q)
//   OP_BMUL: slot C = [slot C +] slot A (x) slot B FIPS 203 Algorithms 11/12
//   OP_ADD : slot C = slot A + slot B
//   OP_SUB : slot C = slot A - slot B
//
// All results are canonical (0 <= x < q).

module mlkem_poly_alu (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,            // Synchronous abort.

    input  logic        start,
    input  logic [2:0]  op,
    input  logic [3:0]  slot_a,
    input  logic [3:0]  slot_b,
    input  logic [3:0]  slot_c,
    input  logic        acc,            // OP_BMUL: accumulate into slot C.
    output logic        done,           // One-cycle pulse.

    // Polynomial RAM ports.
    output logic        ra_en,
    output logic        ra_we,
    output logic [10:0] ra_addr,
    output logic [23:0] ra_din,
    input  logic [23:0] ra_dout,
    output logic        rb_en,
    output logic        rb_we,
    output logic [10:0] rb_addr,
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

    // ---------------------------------------------------------------------
    // Multipliers and constant ROM.
    // ---------------------------------------------------------------------
    logic [11:0] m0_a, m0_b, m0_r;
    logic [11:0] m1_a, m1_b, m1_r;

    mlkem_modmul u_mul0 (.clk(clk), .a(m0_a), .b(m0_b), .r(m0_r));
    mlkem_modmul u_mul1 (.clk(clk), .a(m1_a), .b(m1_b), .r(m1_r));

    // ---------------------------------------------------------------------
    // Control state.
    // ---------------------------------------------------------------------
    typedef enum logic [2:0] {
        S_IDLE,
        S_ISSUE,    // Issue RAM reads.
        S_LATCH,    // Capture operands (and issue accumulator read).
        S_LATCH_C,  // Capture accumulator operand.
        S_EXEC,     // Multiplier pipeline.
        S_WRITE     // Write results, advance.
    } state_t;

    state_t      state;
    logic [2:0]  cur_op;
    logic        scale;         // INTT final pass (x 128^-1).
    logic [3:0]  sa, sb, sc;
    logic        acc_r;
    logic [2:0]  layer;         // NTT/INTT layer 0..6.
    logic [6:0]  item;          // Butterfly-word pair (0..63) or word (0..127).
    logic [3:0]  cnt;           // Cycle counter inside S_EXEC.

    logic [11:0] a0, a1, b0, b1, c0, c1;   // Operands.
    logic [11:0] r00, r11;                 // BMUL partial products.
    logic [11:0] h1;
    logic [11:0] zeta_r;
    logic [23:0] wa, wb;                   // Results to write.

    logic [6:0]  zeta_idx;
    logic [11:0] zeta_val, gamma_val;

    mlkem_zetas u_zetas (
        .zeta_idx  (zeta_idx),
        .zeta      (zeta_val),
        .gamma_idx (item[6:0]),
        .gamma     (gamma_val)
    );

    // Butterfly word addressing for layer `layer`.
    //   NTT : lenw = 64 >> layer (len = 128 >> layer coefficients)
    //   INTT: lenw = 1  << layer (len = 2   << layer coefficients)
    logic [2:0]  lg_lenw;       // log2(lenw)
    logic [6:0]  lenw, jw, jb, blk;
    always_comb begin
        lg_lenw = (cur_op == OP_NTT) ? 3'(6 - layer) : layer;
        lenw    = 7'd1 << lg_lenw;
        blk     = item >> lg_lenw;                         // Block index.
        jw      = ((item >> lg_lenw) << (lg_lenw + 3'd1)) | (item & (lenw - 7'd1));
        jb      = jw + lenw;
        // NTT:  zeta index = 2^layer + blk        (k increments from 1)
        // INTT: zeta index = 2^(7-layer) - 1 - blk (k decrements from 127)
        if (cur_op == OP_NTT)
            zeta_idx = (7'd1 << layer) + blk;
        else
            zeta_idx = 7'((8'd1 << (3'd7 - layer)) - 8'd1 - {1'b0, blk});
    end

    logic butterfly;
    assign butterfly = (cur_op == OP_NTT || cur_op == OP_INTT) && !scale;

    // Multiplier operand selection (combinational, S_EXEC).
    always_comb begin
        m0_a = '0; m0_b = '0; m1_a = '0; m1_b = '0;
        if (state == S_EXEC) begin
            if (scale) begin
                m0_a = a0; m0_b = N_INV;
                m1_a = a1; m1_b = N_INV;
            end else if (cur_op == OP_NTT) begin
                m0_a = b0; m0_b = zeta_r;
                m1_a = b1; m1_b = zeta_r;
            end else if (cur_op == OP_INTT) begin
                m0_a = msub(b0, a0); m0_b = zeta_r;
                m1_a = msub(b1, a1); m1_b = zeta_r;
            end else if (cur_op == OP_BMUL) begin
                case (cnt)
                    4'd0: begin m0_a = a0; m0_b = b0; m1_a = a1; m1_b = b1; end
                    4'd1: begin m0_a = a0; m0_b = b1; m1_a = a1; m1_b = b0; end
                    4'd4: begin m0_a = m1_r; m0_b = gamma_val; end     // (a1*b1)*gamma
                    default: ;
                endcase
            end
        end
    end

    // Last EXEC cycle per operation.
    logic [3:0] exec_last;
    always_comb begin
        if (cur_op == OP_BMUL)                     exec_last = 4'd9;
        else if (cur_op == OP_ADD || cur_op == OP_SUB) exec_last = 4'd0;
        else                                       exec_last = 4'd4;
    end

    // RAM port drive.
    always_comb begin
        ra_en = 1'b0; ra_we = 1'b0; ra_addr = '0; ra_din = wa;
        rb_en = 1'b0; rb_we = 1'b0; rb_addr = '0; rb_din = wb;
        case (state)
            S_ISSUE: begin
                ra_en = 1'b1;
                if (butterfly) begin
                    ra_addr = {sa, jw};
                    rb_en   = 1'b1;
                    rb_addr = {sa, jb};
                end else if (scale) begin
                    ra_addr = {sa, item};
                end else begin
                    ra_addr = {sa, item};
                    rb_en   = 1'b1;
                    rb_addr = {sb, item};
                end
            end
            S_LATCH: begin
                if (cur_op == OP_BMUL && acc_r) begin
                    ra_en   = 1'b1;
                    ra_addr = {sc, item};
                end
            end
            S_WRITE: begin
                ra_en = 1'b1;
                ra_we = 1'b1;
                if (butterfly) begin
                    ra_addr = {sa, jw};
                    rb_en   = 1'b1;
                    rb_we   = 1'b1;
                    rb_addr = {sa, jb};
                end else if (scale) begin
                    ra_addr = {sa, item};
                end else begin
                    ra_addr = {sc, item};
                end
            end
            default: ;
        endcase
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state  <= S_IDLE;
            done   <= 1'b0;
            cur_op <= OP_NTT;
            scale  <= 1'b0;
            sa <= '0; sb <= '0; sc <= '0;
            acc_r  <= 1'b0;
            layer  <= '0;
            item   <= '0;
            cnt    <= '0;
            a0 <= '0; a1 <= '0; b0 <= '0; b1 <= '0; c0 <= '0; c1 <= '0;
            r00 <= '0; r11 <= '0; h1 <= '0;
            zeta_r <= '0;
            wa <= '0; wb <= '0;
        end else if (clr) begin
            state <= S_IDLE;
            done  <= 1'b0;
        end else begin
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    if (start) begin
                        cur_op <= op;
                        scale  <= 1'b0;
                        sa     <= slot_a;
                        sb     <= slot_b;
                        sc     <= slot_c;
                        acc_r  <= acc;
                        layer  <= '0;
                        item   <= '0;
                        state  <= S_ISSUE;
                    end
                end

                S_ISSUE: begin
                    zeta_r <= zeta_val;
                    state  <= S_LATCH;
                end

                S_LATCH: begin
                    a0 <= ra_dout[11:0];  a1 <= ra_dout[23:12];
                    b0 <= rb_dout[11:0];  b1 <= rb_dout[23:12];
                    c0 <= '0;             c1 <= '0;
                    cnt <= '0;
                    state <= (cur_op == OP_BMUL && acc_r) ? S_LATCH_C : S_EXEC;
                end

                S_LATCH_C: begin
                    c0 <= ra_dout[11:0];
                    c1 <= ra_dout[23:12];
                    state <= S_EXEC;
                end

                S_EXEC: begin
                    cnt <= cnt + 4'd1;
                    if (scale) begin
                        if (cnt == 4'd4) wa <= {m1_r, m0_r};
                    end else begin
                        case (cur_op)
                            OP_NTT: if (cnt == 4'd4) begin
                                wa <= {madd(a1, m1_r), madd(a0, m0_r)};
                                wb <= {msub(a1, m1_r), msub(a0, m0_r)};
                            end
                            OP_INTT: if (cnt == 4'd4) begin
                                wa <= {madd(a1, b1), madd(a0, b0)};
                                wb <= {m1_r, m0_r};
                            end
                            OP_ADD: wa <= {madd(a1, b1), madd(a0, b0)};
                            OP_SUB: wa <= {msub(a1, b1), msub(a0, b0)};
                            OP_BMUL: begin
                                // c0: a0b0, a1b1 issued; c1: a0b1, a1b0 issued.
                                if (cnt == 4'd4) begin
                                    r00 <= m0_r;            // a0*b0
                                    r11 <= m1_r;            // a1*b1 (x gamma issued now)
                                end
                                if (cnt == 4'd5) begin
                                    h1  <= madd(m0_r, m1_r); // a0*b1 + a1*b0
                                end
                                if (cnt == 4'd8)
                                    r11 <= madd(r00, m0_r); // h0 = a0*b0 + a1*b1*gamma
                                if (cnt == 4'd9)
                                    wa <= {madd(c1, h1), madd(c0, r11)};
                            end
                            default: ;
                        endcase
                    end
                    if (cnt == exec_last)
                        state <= S_WRITE;
                end

                S_WRITE: begin
                    state <= S_ISSUE;
                    if (butterfly) begin
                        if (item == 7'd63) begin
                            item <= '0;
                            if (layer == 3'd6) begin
                                if (cur_op == OP_INTT) begin
                                    scale <= 1'b1;
                                end else begin
                                    state <= S_IDLE;
                                    done  <= 1'b1;
                                end
                            end else begin
                                layer <= layer + 3'd1;
                            end
                        end else begin
                            item <= item + 7'd1;
                        end
                    end else begin
                        if (item == 7'd127) begin
                            item  <= '0;
                            state <= S_IDLE;
                            done  <= 1'b1;
                        end else begin
                            item <= item + 7'd1;
                        end
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
