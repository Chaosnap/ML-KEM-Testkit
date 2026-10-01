// mlkem_modmul.sv - Pipelined modular multiplier for q = 3329
//
// r = (a * b) mod q for a, b < q, using Barrett reduction with
// m = floor(2^24 / q) = 5039. For a*b < q^2 < 2^24 the quotient estimate
// is off by at most one, so a single conditional subtraction suffices.
//
// Latency: 4 cycles (r is valid LATENCY cycles after a/b are presented).
// Fully pipelined, no handshake. Only the variable product a * b uses a
// DSP48; the two constant multiplies are shift-add networks in LUTs:
//   p1 * 5039 = (p1 << 12) + (p1 << 10) - (p1 << 6) - (p1 << 4) - p1   (CSD)
//   qh * 3329 = (qh << 11) + (qh << 10) + (qh << 8) + qh, low 13 bits only

module mlkem_modmul (
    input  logic        clk,
    input  logic [11:0] a,
    input  logic [11:0] b,
    output logic [11:0] r
);

    localparam logic [12:0] Q = 13'd3329;

    logic [23:0] p1;              // Stage 1: product (DSP).
    logic [12:0] p2;              // Stage 2: product low bits (delayed).
    logic [12:0] qh2;             // Stage 2: quotient estimate.
    logic [12:0] p3;              // Stage 3: product low bits.
    logic [12:0] qq3;             // Stage 3: (qh * q) low bits.

    /* verilator lint_off UNUSEDSIGNAL */
    (* use_dsp = "no" *) logic [36:0] pm;   // p1 * 5039 (only [36:24] used)
    /* verilator lint_on UNUSEDSIGNAL */
    (* use_dsp = "no" *) logic [12:0] qq;   // (qh2 * 3329) mod 2^13
    logic [12:0] t;

    assign pm = (37'(p1) << 12) + (37'(p1) << 10)
              - (37'(p1) << 6) - (37'(p1) << 4) - 37'(p1);
    assign qq = (qh2 << 11) + (qh2 << 10) + (qh2 << 8) + qh2;
    assign t  = p3 - qq3;         // 0 <= t < 2q (fits in 13 bits).

    always_ff @(posedge clk) begin
        p1  <= a * b;
        p2  <= p1[12:0];
        qh2 <= pm[36:24];
        p3  <= p2;
        qq3 <= qq;
        r   <= (t >= Q) ? 12'(t - Q) : t[11:0];
    end

endmodule
