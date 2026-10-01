// keccak_sponge.sv - Lane-oriented SHA-3 / SHAKE sponge (FIPS 202)
//
// The hash engine for every ML-KEM function: G (SHA3-512), H (SHA3-256),
// J / PRF (SHAKE256) and XOF (SHAKE128). Absorb and squeeze move up to one
// 64-bit lane (8 bytes) per cycle, so a full SHAKE128 block takes 21 + 24
// = 45 cycles instead of 168 + 24. The sponge keeps a single 1600-bit
// state and runs keccak_round on it in place (one round per cycle).
//
// Usage:
//   1. Pulse init with mode (0=SHA3-256, 1=SHA3-512, 2=SHAKE128, 3=SHAKE256)
//      while idle.
//   2. Absorb: absorb_valid with absorb_n (1..8) bytes in absorb_data (byte 0
//      = first message byte), accepted when absorb_ready. A chunk must not
//      cross a lane boundary: (bytes absorbed so far mod 8) + absorb_n <= 8.
//   3. Pulse finalize while absorb_ready: applies domain suffix + pad10*1.
//   4. Squeeze: while squeeze_valid, squeeze_data holds the next
//      squeeze_avail (1..8) output bytes (byte 0 first, up to the end of the
//      current lane); squeeze_take with squeeze_n (1..squeeze_avail)
//      consumes squeeze_n of them. SHAKE output is unbounded.
//
// Full rate blocks are permuted automatically in both directions; absorb /
// squeeze are not ready during the 24 permutation cycles.

module keccak_sponge (
    input  logic        clk,
    input  logic        rst_n,

    input  logic        init,           // Clear state and select mode.
    input  logic [1:0]  mode,
    output logic        idle,           // No permutation in flight.

    input  logic        absorb_valid,
    input  logic [63:0] absorb_data,
    input  logic [3:0]  absorb_n,
    output logic        absorb_ready,
    input  logic        finalize,

    output logic        squeeze_valid,
    output logic [63:0] squeeze_data,
    output logic [3:0]  squeeze_avail,
    input  logic        squeeze_take,
    input  logic [3:0]  squeeze_n
);

    logic [1599:0] st;          // Sponge state; lane i = st[64*i +: 64].
    logic [1599:0] st_round;
    logic [7:0]    pos;         // Byte position within the rate.
    logic [7:0]    rate;        // Rate in bytes (multiple of 8).
    logic [7:0]    dsuffix;     // Domain separation + first padding bit.
    logic          squeezing;
    logic          running;     // Permutation in progress.
    logic [4:0]    round;

    keccak_round u_round (
        .s_in  (st),
        .round (round),
        .s_out (st_round)
    );

    logic [4:0] lane;           // Lane of pos.
    logic [2:0] off;            // Byte offset of pos within its lane.
    assign lane = pos[7:3];
    assign off  = pos[2:0];

    assign idle          = !running;
    assign absorb_ready  = !running && !squeezing;
    assign squeeze_valid = !running && squeezing;
    assign squeeze_avail = 4'd8 - 4'(off);

    // Current lane (rate lanes only: 0..20).
    logic [63:0] cur_lane;
    always_comb begin
        cur_lane = '0;
        for (int i = 0; i < 21; i++)
            if (lane == 5'(i))
                cur_lane = st[64 * i +: 64];
    end
    assign squeeze_data = cur_lane >> {off, 3'b000};

    // Absorb chunk, aligned to its position in the lane.
    logic [63:0] ab_mask, ab_lane;
    assign ab_mask = (absorb_n >= 4'd8) ? 64'hFFFF_FFFF_FFFF_FFFF
                                        : ((64'd1 << {absorb_n, 3'b000}) - 64'd1);
    assign ab_lane = (absorb_data & ab_mask) << {off, 3'b000};

    logic do_absorb, do_squeeze, do_final;
    logic [7:0] step, pos_next;
    assign do_final   = absorb_ready && finalize;
    assign do_absorb  = absorb_ready && absorb_valid && !finalize;
    assign do_squeeze = squeeze_valid && squeeze_take;
    assign step       = do_absorb ? 8'(absorb_n) : 8'(squeeze_n);
    assign pos_next   = pos + step;

    // Control registers (async reset).
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pos       <= '0;
            rate      <= 8'd136;
            dsuffix   <= 8'h06;
            squeezing <= 1'b0;
            running   <= 1'b0;
            round     <= '0;
        end else if (running) begin
            round <= round + 5'd1;
            if (round == 5'd23)
                running <= 1'b0;
        end else if (init) begin
            pos       <= '0;
            squeezing <= 1'b0;
            case (mode)
                2'd0: begin rate <= 8'd136; dsuffix <= 8'h06; end  // SHA3-256
                2'd1: begin rate <= 8'd72;  dsuffix <= 8'h06; end  // SHA3-512
                2'd2: begin rate <= 8'd168; dsuffix <= 8'h1F; end  // SHAKE128
                2'd3: begin rate <= 8'd136; dsuffix <= 8'h1F; end  // SHAKE256
            endcase
        end else if (do_final) begin
            pos       <= '0;
            squeezing <= 1'b1;
            running   <= 1'b1;
            round     <= '0;
        end else if (do_absorb || do_squeeze) begin
            if (pos_next == rate) begin
                pos     <= '0;
                running <= 1'b1;
                round   <= '0;
            end else begin
                pos <= pos_next;
            end
        end
    end

    // State (no reset: HINIT clears it before every use, and leaving the
    // 1600 flip-flops off the asynchronous reset net keeps recovery timing
    // and routing in check).
    always_ff @(posedge clk) begin
        if (running) begin
            st <= st_round;
        end else if (init) begin
            st <= '0;
        end else if (do_final) begin
            // M || suffix || 0* || 1 within the current block; rate is a
            // multiple of 8, so the final 0x80 is the top byte of lane
            // rate/8 - 1 (possibly the same lane as the suffix).
            for (int i = 0; i < 21; i++) begin
                logic [63:0] pad;
                pad = '0;
                if (lane == 5'(i))
                    pad = pad ^ (64'(dsuffix) << {off, 3'b000});
                if (rate[7:3] - 5'd1 == 5'(i))
                    pad = pad ^ 64'h8000_0000_0000_0000;
                st[64 * i +: 64] <= st[64 * i +: 64] ^ pad;
            end
        end else if (do_absorb) begin
            for (int i = 0; i < 21; i++)
                if (lane == 5'(i))
                    st[64 * i +: 64] <= st[64 * i +: 64] ^ ab_lane;
        end
    end

endmodule
