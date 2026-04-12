// mlkem_compress.sv - Compress and decompress for ML-KEM
//
// Implements Compress_d and Decompress_d (FIPS 203, Section 4.2.1).
//
// Compress_d(x) = round((2^d / q) * x) mod 2^d
// Decompress_d(y) = round((q / 2^d) * y)
//
// These are used for encoding ciphertext components in ML-KEM:
//   - Compress_du (du=10 for 768, du=11 for 1024) for vector u
//   - Compress_dv (dv=4 for 768, dv=5 for 1024) for scalar v
//
// The division by q is implemented as multiplication by a precomputed
// reciprocal to avoid dividers in hardware.

module mlkem_compress #(
    parameter int D = 10,       // Compression depth in bits.
    parameter int Q = 3329      // Modulus.
) (
    input  logic [11:0] x,              // Input coefficient [0, q-1].
    output logic [D-1:0] compressed     // Output compressed value [0, 2^d - 1].
);

    // Compress_d(x) = round((2^d * x) / q)
    //               = floor((2^d * x + q/2) / q)
    //
    // To avoid division: multiply by precomputed reciprocal.
    // (2^d * x + q/2) / q = x * (2^d / q) + 1/2
    //
    // We compute: floor((x * 2^(d + bit_extra) + q * 2^(bit_extra-1)) / (q * 2^bit_extra))
    // Simplified: floor((x << d + q/2) / q)
    //
    // For synthesis, use the identity:
    //   floor((x * (2^(d+11)) + (q << 10)) >> 23) for d <= 12
    // This works because 2^23 / q ~= 2519, giving enough precision.

    localparam int SHIFT = D + 11;  // Extra precision bits.
    localparam int HALF_Q = Q / 2;

    logic [SHIFT:0] numerator;

    always_comb begin
        numerator  = ({1'b0, x} << D) + HALF_Q;
        // Divide by q using reciprocal multiplication.
        // For q=3329: 1/q ~= 2519/2^23. So (x << d + q/2) * 2519 >> 23.
        // But simpler: just use the direct formula since synthesis tools
        // optimize constant division well for small constants.
        compressed = (numerator / Q) & ((1 << D) - 1);
    end

endmodule

module mlkem_decompress #(
    parameter int D = 10,
    parameter int Q = 3329
) (
    input  logic [D-1:0] y,             // Compressed value [0, 2^d - 1].
    output logic [11:0] decompressed    // Output coefficient [0, q-1].
);

    // Decompress_d(y) = round((q * y) / 2^d)
    //                 = floor((q * y + 2^(d-1)) / 2^d)

    localparam int HALF = 1 << (D - 1);

    logic [23:0] numerator;

    always_comb begin
        numerator    = Q * {{(24-D){1'b0}}, y} + HALF;
        decompressed = numerator[D +: 12];  // Right shift by D, take 12 bits.
    end

endmodule
