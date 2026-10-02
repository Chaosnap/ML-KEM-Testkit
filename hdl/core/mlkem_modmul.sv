// mlkem_modmul.sv - Pipelined modular multiplier for q = 3329
//
// r = (a * b) mod q for a, b < q, using Barrett reduction with
// m = floor(2^24 / q) = 5039. For a*b < q^2 < 2^24 the quotient estimate
// is off by at most one, so a single conditional subtraction suffices.
//
// Latency: 7 cycles (r is valid LATENCY cycles after a/b are presented).
// Fully pipelined, no handshake; at most one adder per stage so it runs
// above 200 MHz on Artix-7 -1. Only a * b uses a DSP48 (stages 1-3 map to
// its A/B, M and P registers); the constant multiplies are shift-add
// networks in LUTs:
//   p * 5039  = (p << 12) + (p << 10) - ((p << 6) + (p << 4) + p)   (CSD)
//   qh * 3329 = (qh << 11) + (qh << 10) + (qh << 8) + qh, low 13 bits only

module mlkem_modmul (
    input  logic        clk,
    input  logic [11:0] a,
    input  logic [11:0] b,
    output logic [11:0] r
);

    localparam logic [12:0] Q = 13'd3329;

    logic [11:0] a0, b0;          // Stage 1: operands (DSP A / B registers).
    logic [23:0] p1;              // Stage 2: product (DSP M register).
    logic [23:0] p2;              // Stage 3: product (DSP P register).
    (* use_dsp = "no" *) logic [36:0] sa4, sb4;   // Stage 4: CSD terms.
    logic [12:0] qh5;             // Stage 5: quotient estimate.
    (* use_dsp = "no" *) logic [12:0] qq6;        // Stage 6: (qh * q) low bits.
    logic [12:0] pl4, pl5, pl6;   // Product low bits.

    /* verilator lint_off UNUSEDSIGNAL */
    logic [36:0] pm;              // p * 5039 (only [36:24] used)
    /* verilator lint_on UNUSEDSIGNAL */
    logic [12:0] t;

    assign pm = sa4 - sb4;
    assign t  = pl6 - qq6;        // 0 <= t < 2q (fits in 13 bits).

    always_ff @(posedge clk) begin
        a0  <= a;
        b0  <= b;
        p1  <= a0 * b0;
        p2  <= p1;
        sa4 <= (37'(p2) << 12) + (37'(p2) << 10);
        sb4 <= (37'(p2) << 6) + (37'(p2) << 4) + 37'(p2);
        pl4 <= p2[12:0];
        qh5 <= pm[36:24];
        pl5 <= pl4;
        qq6 <= ((qh5 << 11) + (qh5 << 10)) + ((qh5 << 8) + qh5);
        pl6 <= pl5;
        r   <= (t >= Q) ? 12'(t - Q) : t[11:0];
    end

endmodule
