// keccak_sponge.sv - Byte-oriented SHA-3 / SHAKE sponge (FIPS 202)
//
// Wraps keccak_f1600 with a byte-serial absorb/squeeze interface. This is
// the hash engine for every ML-KEM function: G (SHA3-512), H (SHA3-256),
// J / PRF (SHAKE256) and XOF (SHAKE128).
//
// Usage:
//   1. Pulse init with mode (0=SHA3-256, 1=SHA3-512, 2=SHAKE128, 3=SHAKE256)
//      while idle.
//   2. Absorb bytes: absorb_valid/absorb_byte, accepted when absorb_ready.
//   3. Pulse finalize while absorb_ready: applies domain suffix + pad10*1.
//   4. Squeeze bytes: squeeze_byte is valid while squeeze_valid; pulse
//      squeeze_take to advance. SHAKE output is unbounded.
//
// Full rate blocks are permuted automatically in both directions.

module keccak_sponge (
    input  logic       clk,
    input  logic       rst_n,

    input  logic       init,           // Clear state and select mode.
    input  logic [1:0] mode,
    output logic       idle,           // No permutation in flight.

    input  logic       absorb_valid,
    input  logic [7:0] absorb_byte,
    output logic       absorb_ready,
    input  logic       finalize,

    output logic       squeeze_valid,
    output logic [7:0] squeeze_byte,
    input  logic       squeeze_take
);

    logic [1599:0] st;          // Sponge state; byte i = st[8*i +: 8].
    logic [7:0]    pos;         // Byte position within the rate.
    logic [7:0]    rate;        // Rate in bytes.
    logic [7:0]    dsuffix;     // Domain separation + first padding bit.
    logic          squeezing;
    logic          perm_start;  // Registered start pulse to keccak_f1600.
    logic          perm_busy;   // Waiting for a permutation we started.

    logic          kf_done, kf_busy;
    logic [1599:0] kf_dout;

    keccak_f1600 u_keccak (
        .clk   (clk),
        .rst_n (rst_n),
        .start (perm_start),
        .done  (kf_done),
        .busy  (kf_busy),
        .din   (st),
        .dout  (kf_dout)
    );

    logic block_end;
    assign block_end = (pos == rate - 8'd1);

    assign idle          = !perm_start && !perm_busy && !kf_busy && !kf_done;
    assign absorb_ready  = idle && !squeezing;
    assign squeeze_valid = idle && squeezing;
    assign squeeze_byte  = st[{pos, 3'b000} +: 8];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st         <= '0;
            pos        <= '0;
            rate       <= 8'd136;
            dsuffix    <= 8'h06;
            squeezing  <= 1'b0;
            perm_start <= 1'b0;
            perm_busy  <= 1'b0;
        end else begin
            perm_start <= 1'b0;

            if (perm_busy && kf_done) begin
                st        <= kf_dout;
                perm_busy <= 1'b0;
            end

            if (init) begin
                st        <= '0;
                pos       <= '0;
                squeezing <= 1'b0;
                case (mode)
                    2'd0: begin rate <= 8'd136; dsuffix <= 8'h06; end  // SHA3-256
                    2'd1: begin rate <= 8'd72;  dsuffix <= 8'h06; end  // SHA3-512
                    2'd2: begin rate <= 8'd168; dsuffix <= 8'h1F; end  // SHAKE128
                    2'd3: begin rate <= 8'd136; dsuffix <= 8'h1F; end  // SHAKE256
                endcase
            end else if (absorb_ready && finalize) begin
                // M || suffix || 0* || 1 within the current block.
                for (int i = 0; i < 168; i++) begin
                    logic [7:0] pad;
                    pad = 8'h00;
                    if (i[7:0] == pos)         pad = pad ^ dsuffix;
                    if (i[7:0] == rate - 8'd1) pad = pad ^ 8'h80;
                    st[8*i +: 8] <= st[8*i +: 8] ^ pad;
                end
                pos        <= '0;
                squeezing  <= 1'b1;
                perm_start <= 1'b1;
                perm_busy  <= 1'b1;
            end else if (absorb_ready && absorb_valid) begin
                st[{pos, 3'b000} +: 8] <= st[{pos, 3'b000} +: 8] ^ absorb_byte;
                if (block_end) begin
                    pos        <= '0;
                    perm_start <= 1'b1;
                    perm_busy  <= 1'b1;
                end else begin
                    pos <= pos + 8'd1;
                end
            end else if (squeeze_valid && squeeze_take) begin
                if (block_end) begin
                    pos        <= '0;
                    perm_start <= 1'b1;
                    perm_busy  <= 1'b1;
                end else begin
                    pos <= pos + 8'd1;
                end
            end
        end
    end

endmodule
