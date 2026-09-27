// keccak_f1600.sv - Keccak-f[1600] permutation core for PQC
//
// Implements the full 24-round Keccak-f[1600] permutation as defined in
// FIPS 202. This is the core primitive used by SHA-3 and SHAKE, which
// underlie all three NIST PQC standards:
//   - ML-KEM (FIPS 203): SHA3-256, SHA3-512, SHAKE-128, SHAKE-256
//   - ML-DSA (FIPS 204): SHAKE-128, SHAKE-256
//   - SLH-DSA (FIPS 205): SHA-256 or SHAKE-256 (this core handles SHAKE)
//
// Architecture:
//   - Single-round iterative: one round per clock cycle, 24 cycles total
//   - State stored in 5x5 array of 64-bit lanes (1600 bits)
//   - Vendor-agnostic: pure combinational logic + registers, no vendor primitives
//
// Interface:
//   - Load state via din when start is asserted
//   - Permutation runs for 24 cycles
//   - Result available on dout when done is asserted
//
// Area: ~3000-5000 LUTs on Xilinx 7-series (depends on synthesis options)
// Latency: 24 clock cycles per permutation

module keccak_f1600 (
    input  logic        clk,
    input  logic        rst_n,

    // Control interface.
    input  logic        start,     // Pulse to load din and begin permutation.
    output logic        done,      // Asserted for one cycle when result is ready.
    output logic        busy,      // High while permutation is running.

    // Data interface: 1600-bit state as flat vector.
    // Lane ordering: state[63:0] = A[0][0], state[127:64] = A[1][0], etc.
    input  logic [1599:0] din,
    output logic [1599:0] dout
);

    // =========================================================================
    // Round constants (iota step)
    // =========================================================================

    // RC[i] for i = 0..23, one per round.
    logic [63:0] RC [0:23];
    assign RC[ 0] = 64'h0000000000000001;
    assign RC[ 1] = 64'h0000000000008082;
    assign RC[ 2] = 64'h800000000000808A;
    assign RC[ 3] = 64'h8000000080008000;
    assign RC[ 4] = 64'h000000000000808B;
    assign RC[ 5] = 64'h0000000080000001;
    assign RC[ 6] = 64'h8000000080008081;
    assign RC[ 7] = 64'h8000000000008009;
    assign RC[ 8] = 64'h000000000000008A;
    assign RC[ 9] = 64'h0000000000000088;
    assign RC[10] = 64'h0000000080008009;
    assign RC[11] = 64'h000000008000000A;
    assign RC[12] = 64'h000000008000808B;
    assign RC[13] = 64'h800000000000008B;
    assign RC[14] = 64'h8000000000008089;
    assign RC[15] = 64'h8000000000008003;
    assign RC[16] = 64'h8000000000008002;
    assign RC[17] = 64'h8000000000000080;
    assign RC[18] = 64'h000000000000800A;
    assign RC[19] = 64'h800000008000000A;
    assign RC[20] = 64'h8000000080008081;
    assign RC[21] = 64'h8000000000008080;
    assign RC[22] = 64'h0000000080000001;
    assign RC[23] = 64'h8000000080008008;

    // =========================================================================
    // Rotation offsets for rho step
    // =========================================================================

    // ROT_OFFSET[x][y] = rotation amount for lane (x, y).
    // A[0][0] is not rotated (offset = 0).
    // (FIPS 202 Table 2, r[x][y]; each row below is one x, columns y=0..4.)
    localparam int ROT_OFFSET [0:4][0:4] = '{
        '{  0, 36,  3, 41, 18},  // x=0
        '{  1, 44, 10, 45,  2},  // x=1
        '{ 62,  6, 43, 15, 61},  // x=2
        '{ 28, 55, 25, 21, 56},  // x=3
        '{ 27, 20, 39,  8, 14}   // x=4
    };

    // =========================================================================
    // State registers and round counter
    // =========================================================================

    logic [63:0] state [0:4][0:4];      // Current state.
    logic [63:0] state_next [0:4][0:4]; // State after one round.
    logic [4:0]  round_cnt;             // Round counter (0-23).

    // =========================================================================
    // State machine
    // =========================================================================

    typedef enum logic [1:0] {
        IDLE    = 2'b00,
        RUNNING = 2'b01,
        DONE    = 2'b10
    } state_t;

    state_t fsm, fsm_next;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fsm <= IDLE;
            round_cnt <= '0;
            for (int x = 0; x < 5; x++)
                for (int y = 0; y < 5; y++)
                    state[x][y] <= '0;
        end else begin
            fsm <= fsm_next;
            case (fsm)
                IDLE: begin
                    if (start) begin
                        // Load input state.
                        for (int x = 0; x < 5; x++)
                            for (int y = 0; y < 5; y++)
                                state[x][y] <= din[(x + 5*y)*64 +: 64];
                        round_cnt <= '0;
                    end
                end
                RUNNING: begin
                    for (int x = 0; x < 5; x++)
                        for (int y = 0; y < 5; y++)
                            state[x][y] <= state_next[x][y];
                    round_cnt <= round_cnt + 1;
                end
                default: ;
            endcase
        end
    end

    always_comb begin
        fsm_next = fsm;
        case (fsm)
            IDLE:    if (start) fsm_next = RUNNING;
            RUNNING: if (round_cnt == 5'd23) fsm_next = DONE;
            DONE:    fsm_next = IDLE;
            default: fsm_next = IDLE;
        endcase
    end

    assign busy = (fsm == RUNNING);
    assign done = (fsm == DONE);

    // Output state as flat vector.
    generate
        for (genvar gx = 0; gx < 5; gx++) begin : gen_out_x
            for (genvar gy = 0; gy < 5; gy++) begin : gen_out_y
                assign dout[(gx + 5*gy)*64 +: 64] = state[gx][gy];
            end
        end
    endgenerate

    // =========================================================================
    // Keccak-f[1600] round function: theta, rho, pi, chi, iota
    // =========================================================================

    // Intermediate values.
    logic [63:0] C [0:4];         // Column parities (theta).
    logic [63:0] D [0:4];         // Column deltas (theta).
    logic [63:0] after_theta [0:4][0:4];
    logic [63:0] after_rho   [0:4][0:4];
    logic [63:0] after_pi    [0:4][0:4];
    logic [63:0] after_chi   [0:4][0:4];

    // --- Theta step ---
    // C[x] = A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4]
    // D[x] = C[(x-1) mod 5] ^ ROT(C[(x+1) mod 5], 1)
    // A'[x][y] = A[x][y] ^ D[x]
    always_comb begin
        for (int x = 0; x < 5; x++) begin
            C[x] = state[x][0] ^ state[x][1] ^ state[x][2]
                 ^ state[x][3] ^ state[x][4];
        end
        for (int x = 0; x < 5; x++) begin
            D[x] = C[(x + 4) % 5] ^ {C[(x + 1) % 5][62:0], C[(x + 1) % 5][63]};
        end
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                after_theta[x][y] = state[x][y] ^ D[x];
    end

    // --- Rho step ---
    // A'[x][y] = ROT(A[x][y], ROT_OFFSET[x][y])
    // Barrel rotation by compile-time constant.
    function automatic logic [63:0] rot64(input logic [63:0] val, input int amt);
        int a;
        a = amt % 64;
        if (a == 0)
            return val;
        else
            return (val << a) | (val >> (64 - a));
    endfunction

    always_comb begin
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                after_rho[x][y] = rot64(after_theta[x][y], ROT_OFFSET[x][y]);
    end

    // --- Pi step ---
    // A'[y][(2*x + 3*y) mod 5] = A[x][y]
    always_comb begin
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                after_pi[y][(2*x + 3*y) % 5] = after_rho[x][y];
    end

    // --- Chi step ---
    // A'[x][y] = A[x][y] ^ (~A[(x+1) mod 5][y] & A[(x+2) mod 5][y])
    always_comb begin
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                after_chi[x][y] = after_pi[x][y]
                                ^ (~after_pi[(x + 1) % 5][y] & after_pi[(x + 2) % 5][y]);
    end

    // --- Iota step ---
    // A'[0][0] = A[0][0] ^ RC[round]
    always_comb begin
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                state_next[x][y] = after_chi[x][y];
        state_next[0][0] = after_chi[0][0] ^ RC[round_cnt];
    end

endmodule
