// keccak_sponge.sv - Lane-oriented SHA-3 / SHAKE sponge (FIPS 202)
//
// The hash engine for every ML-KEM function: G (SHA3-512), H (SHA3-256),
// J / PRF (SHAKE256) and XOF (SHAKE128). Absorb and squeeze move up to one
// 64-bit lane (8 bytes) per cycle. The sponge keeps a single 1600-bit
// state and runs each Keccak round in two fixed cycles: register the
// 320-bit theta correction, then apply theta/rho/pi/chi/iota in place.
// During a permutation, the idle absorb/squeeze registers hold 256 of
// those correction bits; only 64 extra data flip-flops are required.
//
// All handshake outputs (idle, absorb_ready, squeeze_valid / data / avail)
// come straight from registers, and accepted commands are registered
// before they touch the state, so the 1600 state flip-flops only see the
// shortened round path, a registered XOR term and registered control.
// This is ordinary timing pipelining, not a side-channel countermeasure.
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
// 21 + 1 + 48 = 70 cycles until absorb_ready returns.

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
    (* max_fanout = 16 *) logic running;     // Permutation in progress.

    logic [319:0] theta_delta, theta_q;
    logic [63:0] theta_hi;
    (* max_fanout = 32 *) logic round_apply; // 0: capture D[x], 1: commit the round.

    keccak_round #(.PRECOMPUTED_THETA(1'b1)) u_round (
        .s_in  (st),
        .round (round),
        .s_out (st_round),
        .theta_saved (theta_q),
        .theta_delta (theta_delta)
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
    (* max_fanout = 16 *) logic init_q;

    // Squeeze: squeeze_data holds lane li, nx1 / nx2 the next two lanes
    // (lookahead, so a take never waits for the lane select).
    logic [4:0] li_p1; // Index of the lane in nx1.
    // Registered one-hot prefetch index: avoids binary decode on the
    // state -> nx2 feedback path. Shifts to zero beyond the rate lanes.
    (* max_fanout = 32 *) logic [20:0] fetch_sel;
    logic [4:0]  nlanes;        // rate / 8.
    logic        sq_load;       // Load lanes 0, 1, 2 after a permutation.
    logic [63:0] nx1, nx2;

    // These registers are never externally valid while running. Reuse them
    // for D[x], which avoids a separate 320-bit pipeline bank. The state XOR
    // command has already been consumed before the first theta capture.
    assign theta_q = {theta_hi, nx2, nx1, squeeze_data, x_data};

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

    // Parallel one-hot lane select, rather than a 21-arm priority chain.
    // Unused indices return zero. The result is registered in nx2.
    logic [63:0] fetch_lane;
    always_comb begin
        fetch_lane = '0;
        for (int i = 0; i < 21; i++)
            fetch_lane |= st[64 * i +: 64] & {64{fetch_sel[i]}};
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
            round_apply   <= 1'b0;
            x_en          <= '0;
            x_pad         <= '0;
            x_go          <= 1'b0;
            init_q        <= 1'b0;
            ab_rdy        <= 1'b0;
            li_p1         <= 5'd1;
            fetch_sel     <= 21'b1000;
            nlanes        <= 5'd17;
            sq_load       <= 1'b0;
            squeeze_valid <= 1'b0;
            squeeze_avail <= 4'd8;
        end else begin
            x_en   <= '0;
            x_pad  <= '0;
            x_go   <= 1'b0;
            init_q <= 1'b0;

            // Two deterministic clocks per round; state is held during
            // theta capture. Absorb/squeeze are inactive throughout.
            if (running && !round_apply)
                {theta_hi, nx2, nx1, squeeze_data, x_data} <= theta_delta;
            if (running) begin
                round_apply <= !round_apply;
                if (round_apply) begin
                    round <= round + 5'd1;
                    if (round == 5'd23) begin
                        running <= 1'b0;
                        if (squeezing)
                            sq_load <= 1'b1;    // Load lanes 0..2 next cycle.
                        else
                            ab_rdy <= 1'b1;
                    end
                end
            end
            if (x_go) begin
                running     <= 1'b1;
                round       <= '0;
                round_apply <= 1'b0;
            end

            // Squeeze output register.
            if (sq_load) begin
                sq_load       <= 1'b0;
                squeeze_data  <= st[63:0];
                nx1           <= st[127:64];
                nx2           <= st[191:128];
                squeeze_avail <= 4'd8;
                squeeze_valid <= 1'b1;
                li_p1         <= 5'd1;
                fetch_sel     <= 21'b1000;
            end else if (do_take) begin
                if (squeeze_n == squeeze_avail) begin   // Lane used up.
                    if (li_p1 == nlanes) begin
                        squeeze_valid <= 1'b0;
                        running       <= 1'b1;          // Next block.
                        round         <= '0;
                        round_apply   <= 1'b0;
                    end else begin
                        squeeze_data  <= nx1;
                        nx1           <= nx2;
                        nx2           <= fetch_lane;
                        squeeze_avail <= 4'd8;
                        li_p1         <= li_p1 + 5'd1;
                        fetch_sel     <= {fetch_sel[19:0], 1'b0};
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

    // State (no reset: HINIT clears it before every use). Express lane
    // enables explicitly, so inactive absorb lanes do not need a masked XOR
    // feedback datapath. The 256 capacity bits only change on init/rounds.
    for (genvar l = 0; l < 25; l++) begin : g_state
        if (l < 21) begin : g_rate
            always_ff @(posedge clk) begin
                if (init_q)
                    st[64*l +: 64] <= '0;
                else if (running && round_apply)
                    st[64*l +: 64] <= st_round[64*l +: 64];
                else begin
                    if (x_en[l])
                        st[64*l +: 63] <= st[64*l +: 63] ^ x_data[62:0];
                    if (x_en[l] || x_pad[l])
                        st[64*l+63] <= st[64*l+63] ^ (x_en[l] && x_data[63]) ^ x_pad[l];
                end
            end
        end else begin : g_capacity
            always_ff @(posedge clk) begin
                if (init_q)
                    st[64*l +: 64] <= '0;
                else if (running && round_apply)
                    st[64*l +: 64] <= st_round[64*l +: 64];
            end
        end
    end

endmodule
