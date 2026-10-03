// keccak_round.sv - One Keccak-f[1600] round (theta, rho, pi, chi, iota)
//
// Purely combinational; shared by keccak_f1600 (standalone permutation)
// and keccak_sponge (which iterates it on its own state register, so the
// sponge keeps a single copy of the 1600-bit state).
//
// State layout (FIPS 202): lane A[x][y] = s[64*(x + 5*y) +: 64]; byte i of
// the byte-oriented state is s[8*i +: 8].

// PRECOMPUTED_THETA separates the column correction from the rest of the
// round. The sponge registers only 5 x 64 correction bits, not another
// 1600-bit state. The standalone permutation retains its one-cycle round.
module keccak_round #(
    parameter bit PRECOMPUTED_THETA = 1'b0
) (
    input  logic [1599:0] s_in,
    input  logic [4:0]    round,      // 0..23, selects the iota constant.
    output logic [1599:0] s_out,
    input  logic [319:0]  theta_saved,
    output logic [319:0]  theta_delta
);

    // Round constants (iota).
    logic [63:0] rc;
    always_comb begin
        case (round)
            5'd0:  rc = 64'h0000000000000001;
            5'd1:  rc = 64'h0000000000008082;
            5'd2:  rc = 64'h800000000000808A;
            5'd3:  rc = 64'h8000000080008000;
            5'd4:  rc = 64'h000000000000808B;
            5'd5:  rc = 64'h0000000080000001;
            5'd6:  rc = 64'h8000000080008081;
            5'd7:  rc = 64'h8000000000008009;
            5'd8:  rc = 64'h000000000000008A;
            5'd9:  rc = 64'h0000000000000088;
            5'd10: rc = 64'h0000000080008009;
            5'd11: rc = 64'h000000008000000A;
            5'd12: rc = 64'h000000008000808B;
            5'd13: rc = 64'h800000000000008B;
            5'd14: rc = 64'h8000000000008089;
            5'd15: rc = 64'h8000000000008003;
            5'd16: rc = 64'h8000000000008002;
            5'd17: rc = 64'h8000000000000080;
            5'd18: rc = 64'h000000000000800A;
            5'd19: rc = 64'h800000008000000A;
            5'd20: rc = 64'h8000000080008081;
            5'd21: rc = 64'h8000000000008080;
            5'd22: rc = 64'h0000000080000001;
            5'd23: rc = 64'h8000000080008008;
            default: rc = 64'h0;
        endcase
    end

    // Rotation offsets r[x][y] (FIPS 202 Table 2).
    function automatic int rot_off(input int x, input int y);
        case (x * 5 + y)
            0:  return 0;   1:  return 36;  2:  return 3;   3:  return 41;  4:  return 18;
            5:  return 1;   6:  return 44;  7:  return 10;  8:  return 45;  9:  return 2;
            10: return 62;  11: return 6;   12: return 43;  13: return 15;  14: return 61;
            15: return 28;  16: return 55;  17: return 25;  18: return 21;  19: return 56;
            20: return 27;  21: return 20;  22: return 39;  23: return 8;   default: return 14;
        endcase
    endfunction

    function automatic logic [63:0] rot64(input logic [63:0] v, input int amt);
        return (amt == 0) ? v : ((v << amt) | (v >> (64 - amt)));
    endfunction

    // Theta correction D[x] = C[x-1] xor ROT(C[x+1], 1).
    function automatic logic [319:0] theta_fn(input logic [1599:0] si);
        logic [63:0] c [0:4];
        logic [319:0] delta;
        for (int x = 0; x < 5; x++)
            c[x] = si[64*x +: 64] ^ si[64*(x+5) +: 64]
                 ^ si[64*(x+10) +: 64] ^ si[64*(x+15) +: 64]
                 ^ si[64*(x+20) +: 64];
        for (int x = 0; x < 5; x++)
            delta[64*x +: 64] = c[(x+4)%5] ^ rot64(c[(x+1)%5], 1);
        return delta;
    endfunction

    // Whole round as a function with local temporaries (no module-level
    // intermediate signals that a simulator could re-trigger on).
    function automatic logic [1599:0] round_fn(input logic [1599:0] si, input logic [63:0] k, input logic [319:0] delta);
        logic [63:0]   a [0:4][0:4];
        logic [63:0]   b [0:4][0:4];     // After theta, rho, pi.
        logic [1599:0] so;
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                a[x][y] = si[64 * (x + 5 * y) +: 64];
        // Rho + pi: B[y][2x+3y] = ROT(A[x][y] ^ D[x], r[x][y]).
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                b[y][(2 * x + 3 * y) % 5] = rot64(a[x][y] ^ delta[64*x +: 64], rot_off(x, y));
        // Chi + iota.
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                so[64 * (x + 5 * y) +: 64] = b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y])
                                             ^ ((x == 0 && y == 0) ? k : 64'h0);
        return so;
    endfunction

    assign theta_delta = theta_fn(s_in);
    assign s_out = round_fn(s_in, rc,
                           PRECOMPUTED_THETA ? theta_saved : theta_delta);

endmodule
