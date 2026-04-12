// mod_reduce.sv - Modular reduction modules for NTT butterfly
//
// Provides synthesizable modular reduction without the % operator.
// Two implementations:
//   1. Barrett reduction for q=8380417 (ML-DSA, 23-bit modulus)
//   2. Lightweight reduction for q=3329 (ML-KEM, 12-bit modulus)
//
// Reference: Barrett, "Implementing the Rivest Shamir and Adleman
// Public Key Encryption Algorithm on a Standard Digital Signal Processor", 1986.

// Barrett reduction for q=8380417 (ML-DSA).
// Given x < q^2 (up to 46 bits), computes x mod q.
//
// Constants:
//   q = 8380417
//   k = 23 (bit width of q)
//   m = floor(2^(2k) / q) = floor(2^46 / 8380417) = 8396807
//
// Algorithm:
//   t = x - q * floor(x * m / 2^(2k))
//   if t >= q: t = t - q
module barrett_reduce_8380417 (
    input  logic [45:0] x,       // Input: 0 <= x < q^2.
    output logic [22:0] r        // Output: x mod q.
);

    localparam logic [22:0] Q = 23'd8380417;
    localparam logic [22:0] M = 23'd8396807;  // floor(2^46 / q).

    logic [68:0] xm;       // x * m (46 + 23 = 69 bits max).
    logic [22:0] q_hat;    // floor(xm / 2^46).
    logic [46:0] qhat_q;   // q_hat * q.
    logic [23:0] t;        // x - qhat_q (may be up to 2q).

    always_comb begin
        xm     = {23'b0, x} * {46'b0, M};
        q_hat  = xm[68:46];                     // Right shift by 46.
        qhat_q = {24'b0, q_hat} * {24'b0, Q};
        t      = x[23:0] - qhat_q[23:0];        // Low bits only (mod 2^24 is fine).

        // At most one conditional subtraction needed.
        if (t >= {1'b0, Q})
            r = t[22:0] - Q;
        else
            r = t[22:0];
    end

endmodule

// Lightweight reduction for q=3329 (ML-KEM).
// Given x < q^2 (up to 24 bits), computes x mod q.
//
// For q=3329, we use the identity: 3329 = 13 * 256 + 1.
// Barrett constant: m = floor(2^24 / 3329) = 5039.
module barrett_reduce_3329 (
    input  logic [23:0] x,       // Input: 0 <= x < q^2.
    output logic [11:0] r        // Output: x mod q.
);

    localparam logic [11:0] Q = 12'd3329;
    localparam logic [12:0] M = 13'd5039;  // floor(2^24 / q).

    logic [36:0] xm;       // x * m.
    logic [12:0] q_hat;    // floor(xm / 2^24).
    logic [24:0] qhat_q;   // q_hat * q.
    logic [12:0] t;

    always_comb begin
        xm     = {13'b0, x} * {24'b0, M};
        q_hat  = xm[36:24];
        qhat_q = {12'b0, q_hat} * {13'b0, Q};
        t      = x[12:0] - qhat_q[12:0];

        if (t >= {1'b0, Q})
            r = t[11:0] - Q;
        else
            r = t[11:0];
    end

endmodule

// Modular addition: (a + b) mod q, where a, b < q.
// Result is at most 2q-2, so one subtraction suffices.
module mod_add #(
    parameter int WIDTH = 23,
    parameter int MODULUS = 8380417
) (
    input  logic [WIDTH-1:0] a,
    input  logic [WIDTH-1:0] b,
    output logic [WIDTH-1:0] r
);
    logic [WIDTH:0] sum;
    assign sum = {1'b0, a} + {1'b0, b};
    assign r = (sum >= MODULUS) ? sum[WIDTH-1:0] - MODULUS[WIDTH-1:0] : sum[WIDTH-1:0];
endmodule

// Modular subtraction: (a - b) mod q, where a, b < q.
// If a < b, add q to avoid underflow.
module mod_sub #(
    parameter int WIDTH = 23,
    parameter int MODULUS = 8380417
) (
    input  logic [WIDTH-1:0] a,
    input  logic [WIDTH-1:0] b,
    output logic [WIDTH-1:0] r
);
    logic [WIDTH:0] diff;
    assign diff = {1'b0, a} - {1'b0, b};
    assign r = diff[WIDTH] ? diff[WIDTH-1:0] + MODULUS[WIDTH-1:0] : diff[WIDTH-1:0];
endmodule
