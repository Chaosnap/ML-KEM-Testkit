// keccak_absorb_squeeze.sv - Absorb/squeeze wrapper for Keccak-f[1600]
//
// Provides the sponge construction interface on top of keccak_f1600.
// Supports SHA-3 (fixed output) and SHAKE (extendable output) modes
// with configurable rate.
//
// Rates (capacity = 1600 - rate):
//   SHA3-256:  rate = 1088 bits (136 bytes)
//   SHA3-512:  rate = 576 bits  (72 bytes)
//   SHAKE-128: rate = 1344 bits (168 bytes)
//   SHAKE-256: rate = 1088 bits (136 bytes)
//
// Interface:
//   1. Assert init to reset state
//   2. Write input blocks via absorb_data/absorb_valid (rate bits at a time)
//   3. Assert absorb_last on final block (handles padding)
//   4. Read output via squeeze_data/squeeze_valid (rate bits per squeeze)
//   5. Assert squeeze_next for additional output blocks (SHAKE XOF mode)

module keccak_absorb_squeeze #(
    parameter int RATE_BITS = 1088  // Default: SHA3-256 / SHAKE-256 rate.
) (
    input  logic        clk,
    input  logic        rst_n,

    // Control.
    input  logic        init,          // Reset sponge state.
    input  logic [1:0]  mode,          // 0=SHA3-256, 1=SHA3-512, 2=SHAKE-128, 3=SHAKE-256.
    output logic        ready,         // Ready to accept input or produce output.

    // Absorb interface.
    input  logic [RATE_BITS-1:0] absorb_data,
    input  logic        absorb_valid,  // Input block is valid.
    input  logic        absorb_last,   // This is the final (padded) block.

    // Squeeze interface.
    output logic [RATE_BITS-1:0] squeeze_data,
    output logic        squeeze_valid, // Output block is valid.
    input  logic        squeeze_next   // Request next output block (XOF).
);

    // =========================================================================
    // Internal signals
    // =========================================================================

    logic [1599:0] state_reg;      // Sponge state.
    logic [1599:0] keccak_din;     // Input to Keccak-f.
    logic [1599:0] keccak_dout;    // Output from Keccak-f.
    logic          keccak_start;
    logic          keccak_done;
    logic          keccak_busy;

    // =========================================================================
    // Keccak-f[1600] permutation instance
    // =========================================================================

    keccak_f1600 u_keccak (
        .clk    (clk),
        .rst_n  (rst_n),
        .start  (keccak_start),
        .done   (keccak_done),
        .busy   (keccak_busy),
        .din    (keccak_din),
        .dout   (keccak_dout)
    );

    // =========================================================================
    // State machine
    // =========================================================================

    typedef enum logic [2:0] {
        S_IDLE     = 3'b000,
        S_ABSORB   = 3'b001,
        S_PERMUTE  = 3'b010,
        S_SQUEEZE  = 3'b011,
        S_SQUEEZE_PERMUTE = 3'b100
    } sponge_state_t;

    sponge_state_t fsm;
    logic absorbing_last;      // Track if we are processing the final block.
    logic keccak_start_reg;    // Registered start pulse (avoids race with state_reg update).

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fsm <= S_IDLE;
            state_reg <= '0;
            absorbing_last <= 1'b0;
            keccak_start_reg <= 1'b0;
        end else begin
            // Default: clear start pulse after one cycle.
            keccak_start_reg <= 1'b0;

            case (fsm)
                S_IDLE: begin
                    if (init) begin
                        state_reg <= '0;
                        absorbing_last <= 1'b0;
                    end else if (absorb_valid) begin
                        // XOR input block into rate portion of state.
                        state_reg[RATE_BITS-1:0] <= state_reg[RATE_BITS-1:0] ^ absorb_data;
                        absorbing_last <= absorb_last;
                        // Transition to ABSORB state; start pulse fires next cycle
                        // after state_reg has been updated.
                        fsm <= S_ABSORB;
                    end
                end

                S_ABSORB: begin
                    // State_reg now contains the XOR'd data from the previous cycle.
                    // Fire the Keccak start pulse with correct state.
                    keccak_start_reg <= 1'b1;
                    fsm <= S_PERMUTE;
                end

                S_PERMUTE: begin
                    // Wait for Keccak-f to complete (24 cycles).
                    if (keccak_done) begin
                        state_reg <= keccak_dout;
                        if (absorbing_last)
                            fsm <= S_SQUEEZE;
                        else
                            fsm <= S_IDLE;
                    end
                end

                S_SQUEEZE: begin
                    // Output is available. Wait for squeeze_next or new absorb.
                    if (squeeze_next) begin
                        keccak_start_reg <= 1'b1;
                        fsm <= S_SQUEEZE_PERMUTE;
                    end
                end

                S_SQUEEZE_PERMUTE: begin
                    // Permute again for next squeeze block.
                    if (keccak_done) begin
                        state_reg <= keccak_dout;
                        fsm <= S_SQUEEZE;
                    end
                end

                default: fsm <= S_IDLE;
            endcase
        end
    end

    // =========================================================================
    // Keccak-f control
    // =========================================================================

    // Registered start pulse ensures state_reg is stable when Keccak samples din.
    assign keccak_start = keccak_start_reg;
    assign keccak_din   = state_reg;

    // =========================================================================
    // Output signals
    // =========================================================================

    assign ready        = (fsm == S_IDLE) || (fsm == S_SQUEEZE);
    assign squeeze_data = state_reg[RATE_BITS-1:0];
    assign squeeze_valid = (fsm == S_SQUEEZE);

endmodule
