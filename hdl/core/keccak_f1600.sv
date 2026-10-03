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
//   - Round function in keccak_round.sv (shared with keccak_sponge, which
//     iterates it on its own state; the ML-KEM core does not instantiate
//     this standalone wrapper)
//   - Vendor-agnostic: pure combinational logic + registers, no vendor primitives
//
// Interface:
//   - Load state via din when start is asserted
//   - Permutation runs for 24 cycles
//   - Result available on dout when done is asserted
//
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

    logic [1599:0] state, state_next;
    logic [4:0]    round_cnt;           // Round counter (0-23).

    keccak_round u_round (
        .s_in  (state),
        .round (round_cnt),
        .s_out (state_next),
        .theta_saved (320'b0),
        .theta_delta ()
    );

    typedef enum logic [1:0] {
        IDLE    = 2'b00,
        RUNNING = 2'b01,
        DONE    = 2'b10
    } state_t;

    state_t fsm, fsm_next;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fsm       <= IDLE;
            round_cnt <= '0;
            state     <= '0;
        end else begin
            fsm <= fsm_next;
            case (fsm)
                IDLE: begin
                    if (start) begin
                        state     <= din;
                        round_cnt <= '0;
                    end
                end
                RUNNING: begin
                    state     <= state_next;
                    round_cnt <= round_cnt + 5'd1;
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
    assign dout = state;

endmodule
