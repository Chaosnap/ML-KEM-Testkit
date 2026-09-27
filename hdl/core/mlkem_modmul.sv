// mlkem_modmul.sv - Pipelined modular multiplier for q = 3329
//
// r = (a * b) mod q for a, b < q, using Barrett reduction with
// m = floor(2^24 / q) = 5039. For a*b < q^2 < 2^24 the quotient estimate
// is off by at most one, so a single conditional subtraction suffices.
//
// Latency: 4 cycles (r is valid LATENCY cycles after a/b are presented).
// Fully pipelined, no handshake. The multiplies map onto DSP48 slices.

module mlkem_modmul (
    input  logic        clk,
    input  logic [11:0] a,
    input  logic [11:0] b,
    output logic [11:0] r
);

    localparam logic [12:0] Q = 13'd3329;
    localparam logic [12:0] M = 13'd5039;

    logic [23:0] p1;              // Stage 1: product.
    logic [23:0] p2;              // Stage 2: product (delayed).
    logic [12:0] qh2;             // Stage 2: quotient estimate.
    logic [12:0] p3;              // Stage 3: product low bits.
    logic [12:0] qq3;             // Stage 3: (qh * q) low bits.

    logic [36:0] pm;
    logic [25:0] qq;
    logic [12:0] t;

    assign pm = p1 * M;
    assign qq = qh2 * Q;
    assign t  = p3 - qq3;         // 0 <= t < 2q (fits in 13 bits).

    always_ff @(posedge clk) begin
        p1  <= a * b;
        p2  <= p1;
        qh2 <= pm[36:24];
        p3  <= p2[12:0];
        qq3 <= qq[12:0];
        r   <= (t >= Q) ? 12'(t - Q) : t[11:0];
    end

endmodule
