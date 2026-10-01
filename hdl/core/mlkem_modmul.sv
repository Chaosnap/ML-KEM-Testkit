// mlkem_modmul.sv - Pipelined modular multiplier for q = 3329
//
// r = (a * b) mod q for a, b < q, using Barrett reduction with
// m = floor(2^24 / q) = 5039. For a*b < q^2 < 2^24 the quotient estimate
// is off by at most one, so a single conditional subtraction suffices.
//
// Latency: 5 cycles (r is valid LATENCY cycles after a/b are presented).
// Fully pipelined, no handshake. Only the variable product a * b uses a
// DSP48 (stages 1-2 map to its M and P registers); the two constant
// multiplies are shift-add networks in LUTs, split over stages 3 and 4:
//   p * 5039  = (p << 12) + (p << 10) - ((p << 6) + (p << 4) + p)   (CSD)
//   qh * 3329 = (qh << 11) + (qh << 10) + (qh << 8) + qh, low 13 bits only

module mlkem_modmul (
    input  logic        clk,
    input  logic [11:0] a,
    input  logic [11:0] b,
    output logic [11:0] r
);

    localparam logic [12:0] Q = 13'd3329;

    logic [23:0] p1;              // Stage 1: product (DSP M register).
    logic [23:0] p2;              // Stage 2: product (DSP P register).
    logic [36:0] sa3, sb3;        // Stage 3: positive / negative CSD terms.
    logic [12:0] pl3, pl4;        // Product low bits.
    logic [12:0] qq4;             // Stage 4: (qh * q) low bits.

    /* verilator lint_off UNUSEDSIGNAL */
    (* use_dsp = "no" *) logic [36:0] pm;   // p * 5039 (only [36:24] used)
    /* verilator lint_on UNUSEDSIGNAL */
    (* use_dsp = "no" *) logic [12:0] qq;
    logic [12:0] qh, t;

    assign pm = sa3 - sb3;
    assign qh = pm[36:24];
    assign qq = (qh << 11) + (qh << 10) + (qh << 8) + qh;
    assign t  = pl4 - qq4;        // 0 <= t < 2q (fits in 13 bits).

    always_ff @(posedge clk) begin
        p1  <= a * b;
        p2  <= p1;
        sa3 <= (37'(p2) << 12) + (37'(p2) << 10);
        sb3 <= (37'(p2) << 6) + (37'(p2) << 4) + 37'(p2);
        pl3 <= p2[12:0];
        qq4 <= qq;
        pl4 <= pl3;
        r   <= (t >= Q) ? 12'(t - Q) : t[11:0];
    end

endmodule
