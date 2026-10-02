// keccak_sponge.sv - Lane-oriented SHA-3 / SHAKE sponge (FIPS 202)
//
// The hash engine for every ML-KEM function: G (SHA3-512), H (SHA3-256),
// J / PRF (SHAKE256) and XOF (SHAKE128). Absorb and squeeze move up to one
// 64-bit lane (8 bytes) per cycle. The sponge keeps a single 1600-bit
// state and runs keccak_round on it in place, one round per cycle.
//
// All handshake outputs (idle, absorb_ready, squeeze_valid / data / avail)
// come straight from registers, and accepted commands are registered
// before they touch the state, so the 1600 state flip-flops only see the
// round function, a registered XOR term and registered control (> 200 MHz).
//
// Usage:
//   1. Pulse init with mode (0=SHA3-256, 1=SHA3-512, 2=SHAKE128, 3=SHAKE256)
//      while idle.
//   2. Absorb: absorb_valid with absorb_n (1..8) bytes in absorb_data (byte 0
//      = first message byte), accepted when absorb_ready. A chunk must not
//      cross a lane boundary: (bytes absorbed so far mod 8) + absorb_n <= 8.
//   3. Pulse finalize while absorb_ready: applies domain suffix + pad10*1.
//   4. Squeeze: while squeeze_valid, squeeze_data holds the next
//      squeeze_avail (8, or 4 after half a lane was taken) output bytes,
//      byte 0 first. squeeze_take consumes squeeze_n bytes: either all of
//      squeeze_avail, or 4 when squeeze_avail is 8. SHAKE output is unbounded.
//
// Timing: a full SHAKE128 block absorbed one lane per cycle takes
// 21 + 1 + 24 = 46 cycles until absorb_ready returns.

module keccak_sponge (
    input  logic        clk,
    input  logic        rst_n,

    input  logic        init,           // Clear state and select mode.
    input  logic [1:0]  mode,
    output logic        idle,           // No command or permutation pending.

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

    // Registered active-high synchronous reset, local to this module
    // (replicated by fanout; no inverter on the high-fanout net).
    (* max_fanout = 32 *) logic srst;
    always_ff @(posedge clk)
        srst <= !rst_n;

    logic [1599:0] st;          // Sponge state; lane i = st[64*i +: 64].
    logic [1599:0] st_round;
    logic [4:0]    round;
    logic          running;     // Permutation in progress.

    keccak_round u_round (
        .s_in  (st),
        .round (round),
        .s_out (st_round)
    );

    // Mode.
    logic [7:0]  rate;          // Rate in bytes (multiple of 8).
    logic [7:0]  dsuffix;       // Domain separation + first padding bit.
    logic [20:0] last_oh;       // One-hot: last lane of the rate (gets 0x80).
    logic        squeezing;

    // Absorb position.
    logic [7:0]  pos;           // Byte position within the rate.
    logic [7:0]  rem;           // rate - pos.
    logic [4:0]  lane;
    logic [2:0]  off;
    assign lane = pos[7:3];
    assign off  = pos[2:0];

    // Registered command applied to the state in the next cycle.
    (* max_fanout = 64 *) logic [20:0] x_en;   // Lanes that get x_data.
    logic [63:0] x_data;
    (* max_fanout = 64 *) logic [20:0] x_pad;  // Lanes whose top bit flips (pad 0x80).
    logic        x_go;          // Start the permutation after this XOR.
    (* max_fanout = 64 *) logic init_q;

    // Squeeze.
    logic [4:0]  li, li_p1;     // Current lane, current lane + 1.
    logic [4:0]  nlanes;        // rate / 8.
    logic        sq_load;       // Load squeeze_data from lane li_p1 / 0.

    // Ready / idle are registers.
    logic        ab_rdy;
    assign absorb_ready = ab_rdy;
    assign idle         = !running && !x_go && !init_q && !sq_load;

    // Accepted absorb chunk, aligned: byte mask from absorb_n (no carry chain).
    logic [63:0] ab_mask, ab_lane;
    always_comb begin
        for (int i = 0; i < 8; i++)
            ab_mask[8 * i +: 8] = (4'(i) < absorb_n) ? 8'hFF : 8'h00;
        ab_lane = (absorb_data & ab_mask) << {off, 3'b000};
    end

    logic do_absorb, do_final, do_take;
    assign do_final  = ab_rdy && finalize;
    assign do_absorb = ab_rdy && absorb_valid && !finalize;
    assign do_take   = squeeze_valid && squeeze_take;

    // Lane one-hot of pos.
    logic [20:0] lane_oh;
    always_comb
        for (int i = 0; i < 21; i++)
            lane_oh[i] = (lane == 5'(i));

    // Lane li_p1 of the state (next squeeze lane).
    logic [63:0] nxt_lane;
    always_comb begin
        nxt_lane = '0;
        for (int i = 0; i < 21; i++)
            if (li_p1 == 5'(i))
                nxt_lane = st[64 * i +: 64];
    end

    always_ff @(posedge clk) begin
        if (srst) begin
            rate          <= 8'd136;
            dsuffix       <= 8'h06;
            last_oh       <= 21'd1 << 16;
            squeezing     <= 1'b0;
            pos           <= '0;
            rem           <= 8'd136;
            running       <= 1'b0;
            round         <= '0;
            x_en          <= '0;
            x_pad         <= '0;
            x_go          <= 1'b0;
            init_q        <= 1'b0;
            ab_rdy        <= 1'b0;
            li            <= '0;
            li_p1         <= 5'd1;
            nlanes        <= 5'd17;
            sq_load       <= 1'b0;
            squeeze_valid <= 1'b0;
            squeeze_avail <= 4'd8;
        end else begin
            x_en   <= '0;
            x_pad  <= '0;
            x_go   <= 1'b0;
            init_q <= 1'b0;

            // Permutation.
            if (running) begin
                round <= round + 5'd1;
                if (round == 5'd23) begin
                    running <= 1'b0;
                    if (squeezing) begin
                        sq_load <= 1'b1;        // Load lane 0 next cycle.
                        li      <= '0;
                        li_p1   <= '0;
                    end else begin
                        ab_rdy  <= 1'b1;
                    end
                end
            end
            if (x_go) begin
                running <= 1'b1;
                round   <= '0;
            end

            // Squeeze output register.
            if (sq_load) begin
                sq_load       <= 1'b0;
                squeeze_data  <= nxt_lane;
                squeeze_avail <= 4'd8;
                squeeze_valid <= 1'b1;
                li_p1         <= li + 5'd1;
            end else if (do_take) begin
                if (squeeze_n == squeeze_avail) begin   // Lane used up.
                    if (li_p1 == nlanes) begin
                        squeeze_valid <= 1'b0;
                        running       <= 1'b1;          // Next block.
                        round         <= '0;
                    end else begin
                        squeeze_data  <= nxt_lane;
                        squeeze_avail <= 4'd8;
                        li            <= li_p1;
                        li_p1         <= li_p1 + 5'd1;
                    end
                end else begin                          // Low half taken.
                    squeeze_data  <= squeeze_data >> 32;
                    squeeze_avail <= 4'd4;
                end
            end

            // Commands.
            if (init) begin
                init_q        <= 1'b1;
                squeezing     <= 1'b0;
                squeeze_valid <= 1'b0;
                ab_rdy        <= 1'b1;
                pos           <= '0;
                case (mode)
                    2'd0: begin rate <= 8'd136; rem <= 8'd136; dsuffix <= 8'h06;
                                last_oh <= 21'd1 << 16; nlanes <= 5'd17; end  // SHA3-256
                    2'd1: begin rate <= 8'd72;  rem <= 8'd72;  dsuffix <= 8'h06;
                                last_oh <= 21'd1 << 8;  nlanes <= 5'd9;  end  // SHA3-512
                    2'd2: begin rate <= 8'd168; rem <= 8'd168; dsuffix <= 8'h1F;
                                last_oh <= 21'd1 << 20; nlanes <= 5'd21; end  // SHAKE128
                    2'd3: begin rate <= 8'd136; rem <= 8'd136; dsuffix <= 8'h1F;
                                last_oh <= 21'd1 << 16; nlanes <= 5'd17; end  // SHAKE256
                endcase
            end else if (do_final) begin
                // M || suffix || 0* || 1 within the current block.
                x_en      <= lane_oh;
                x_data    <= 64'(dsuffix) << {off, 3'b000};
                x_pad     <= last_oh;
                x_go      <= 1'b1;
                ab_rdy    <= 1'b0;
                squeezing <= 1'b1;
                pos       <= '0;
                rem       <= rate;
            end else if (do_absorb) begin
                x_en   <= lane_oh;
                x_data <= ab_lane;
                if (rem[7:4] == 4'd0 && rem[3:0] == absorb_n) begin   // Block full.
                    x_go   <= 1'b1;
                    ab_rdy <= 1'b0;
                    pos    <= '0;
                    rem    <= rate;
                end else begin
                    pos <= pos + 8'(absorb_n);
                    rem <= rem - 8'(absorb_n);
                end
            end
        end
    end

    // State (no reset: HINIT clears it before every use). Only registered
    // controls and data reach these 1600 flip-flops.
    always_ff @(posedge clk) begin
        if (init_q)
            st <= '0;
        else if (running)
            st <= st_round;
        else
            for (int i = 0; i < 21; i++)
                st[64 * i +: 64] <= st[64 * i +: 64]
                                  ^ (x_en[i] ? x_data : 64'h0)
                                  ^ {x_pad[i], 63'h0};
    end

endmodule
