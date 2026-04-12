// mlkem_cbd.sv - Centered Binomial Distribution sampler for ML-KEM
//
// Implements CBD_eta (FIPS 203, Algorithm 8) which samples polynomial
// coefficients from a centered binomial distribution.
//
// For ML-KEM:
//   - eta=2 (ML-KEM-768, ML-KEM-1024): CBD_2 samples from {-2,-1,0,1,2}
//   - eta=3 (ML-KEM-512): CBD_3 samples from {-3,-2,-1,0,1,2,3}
//
// CBD_eta(B):
//   For each coefficient i:
//     x = sum(B[2*eta*i + j] for j in 0..eta-1)
//     y = sum(B[2*eta*i + eta + j] for j in 0..eta-1)
//     f[i] = x - y
//
// This module processes one coefficient per clock cycle from a byte stream.

module mlkem_cbd #(
    parameter int ETA = 2    // 2 for ML-KEM-768/1024, 3 for ML-KEM-512.
) (
    input  logic        clk,
    input  logic        rst_n,

    // Input: random bytes from SHAKE/PRF output.
    input  logic [2*ETA-1:0] bits_in,   // 2*eta bits per coefficient.
    input  logic        valid_in,

    // Output: sampled coefficient in range [-eta, eta], encoded mod q=3329.
    output logic [11:0] coeff_out,      // Coefficient mod 3329.
    output logic        valid_out
);

    localparam int Q = 3329;

    // Popcount function: count number of set bits.
    function automatic logic [$clog2(ETA+1)-1:0] popcount(
        input logic [ETA-1:0] val
    );
        logic [$clog2(ETA+1)-1:0] cnt;
        cnt = '0;
        for (int i = 0; i < ETA; i++)
            cnt = cnt + {{($clog2(ETA+1)-1){1'b0}}, val[i]};
        return cnt;
    endfunction

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            coeff_out <= '0;
            valid_out <= 1'b0;
        end else begin
            valid_out <= valid_in;
            if (valid_in) begin
                // Split input bits into two halves.
                logic [ETA-1:0] a_bits, b_bits;
                logic [$clog2(ETA+1)-1:0] x, y;
                logic signed [12:0] diff;

                a_bits = bits_in[ETA-1:0];
                b_bits = bits_in[2*ETA-1:ETA];
                x = popcount(a_bits);
                y = popcount(b_bits);

                // diff = x - y, range [-eta, eta].
                diff = $signed({1'b0, x}) - $signed({1'b0, y});

                // Convert to mod q: if negative, add q.
                if (diff < 0)
                    coeff_out <= 12'(Q + diff);
                else
                    coeff_out <= 12'(diff);
            end
        end
    end

endmodule
