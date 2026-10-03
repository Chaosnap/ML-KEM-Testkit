// mlkem_pack.sv - Polynomial to byte stream for ML-KEM
//
// Reads 256 coefficients from a poly RAM slot and emits
// ByteEncode_d(Compress_d(f)) (FIPS 203 section 4.2.1) as 32-bit
// little-endian words. For d = 12 the coefficients are encoded without
// compression. 256 * d is a multiple of 32, so the stream ends on a word.
//
// Compress_d(x) = round(2^d * x / q) mod 2^d
//               = floor(((x << d) + 1664) / 3329) mod 2^d
// For n=(x<<d)+1664, floor(n/q)=(n*161271)>>29 in the supported
// domain x<3329, 1<=d<=11. Move the d-dependent scaling to the bias:
//   floor((x*161271*2^d+C)/2^29)
//     = floor((x*161271+floor(C/2^d))/2^(29-d)), C=1664*161271.
// Compression keeps only the low d bits, so only sum bits [28:29-d]
// matter. A 29-bit add and fixed output slices replace the old 41-bit
// variable left shifter plus 41-bit add. d=12 still bypasses compression.
// The constant multiply uses the same shift/add network, no extra DSP.
//
// Pipeline, one coefficient per cycle, at most one adder per stage
// (c = coefficient whose word is read at cycle t; RAM read latency 2):
//   t-1  read command registered (even c only)   t+5  x * 161271 + predecoded bias
//   t+2  RAM word registered                     t+6  fixed slice, Compress_d
//   t+3  x, CSD partial sums                     t+7  value << bit position
//   t+4  x * 161271                              t+8  merge, emit a full word

module mlkem_pack (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,

    input  logic        start,
    input  logic [3:0]  d,
    input  logic [2:0]  slot,
    output logic        done,       // One-cycle pulse (with the last word).

    // Poly RAM read port (registered).
    output logic        rd_en,
    output logic [9:0]  rd_addr,
    input  logic [23:0] rd_data,

    // Word sink (always accepted).
    output logic        out_valid,
    output logic [31:0] out_word
);

    // Registered active-high synchronous reset, local to this module
    // (replicated by fanout; no inverter on the high-fanout net).
    (* max_fanout = 32 *) logic srst;
    always_ff @(posedge clk)
        srst <= !rst_n;

    localparam logic [28:0] RECIP_1664 = 29'd268354944;   // 1664 * 161271

    logic        running;
    logic [3:0]  d_r;
    logic [2:0]  slot_r;
    logic [8:0]  ic;                // Next coefficient to issue (0..256).
    logic        issue;
    assign issue = running && !ic[8];

    // Pipeline valid / half bits: pv[i] during t+i.
    logic [8:0]  pv;
    logic [3:0]  ph;                // Coefficient half (odd c), ph[i] during t+i.

    logic [23:0] rw;                // t+3: RAM word.
    logic [11:0] x4, x5, x6;        // Coefficient (d = 12 passes it through).
    (* use_dsp = "no" *) logic [29:0] sa4, sb4, sc4;   // t+4: CSD partial sums.
    (* use_dsp = "no" *) logic [29:0] xr5;             // t+5: x * 161271.
    logic [26:0] bias_r;            // floor(1664 * 161271 / 2^d), set at start.
    logic [28:0] scaled6;           // t+6: low 29 bits of x*161271 + bias.
    logic [11:0] val7;              // t+7: compressed value.
    logic [43:0] sh8;               // t+8: value at its bit position.
    logic        em8;               // t+8: this value completes a word.
    logic [43:0] acc;               // Pending bits.
    logic [5:0]  nb;                // Pending bit count (< 32), at stage t+7.
    logic [8:0]  cnt;               // Coefficients merged.

    logic [11:0] x3;
    logic [29:0] e3;
    logic [6:0]  nb_next;
    assign x3      = ph[3] ? rw[23:12] : rw[11:0];
    assign e3      = 30'(x3);
    assign nb_next = 7'(nb) + 7'(d_r);

    logic [43:0] merged;
    assign merged = acc | sh8;

    always_ff @(posedge clk) begin
        // Registered read command for the next cycle.
        rd_en   <= issue && !ic[0];
        rd_addr <= {slot_r, ic[7:1]};
        // Datapath.
        rw   <= rd_data;                                     // end of t+2
        x4   <= x3;                                          // end of t+3
        sa4  <= (e3 << 17) + (e3 << 15);
        sb4  <= (e3 << 11) + (e3 << 9);
        sc4  <= (e3 << 3) + e3;
        x5   <= x4;                                          // end of t+4
        xr5  <= (sa4 - sb4) - sc4;
        x6   <= x5;                                          // end of t+5
        scaled6 <= xr5[28:0] + {2'b0, bias_r};                // end of t+5
        case (d_r)                                          // end of t+6
            4'd1: val7 <= 12'(scaled6[28:28]);
            4'd2: val7 <= 12'(scaled6[28:27]);
            4'd3: val7 <= 12'(scaled6[28:26]);
            4'd4: val7 <= 12'(scaled6[28:25]);
            4'd5: val7 <= 12'(scaled6[28:24]);
            4'd6: val7 <= 12'(scaled6[28:23]);
            4'd7: val7 <= 12'(scaled6[28:22]);
            4'd8: val7 <= 12'(scaled6[28:21]);
            4'd9: val7 <= 12'(scaled6[28:20]);
            4'd10: val7 <= 12'(scaled6[28:19]);
            4'd11: val7 <= 12'(scaled6[28:18]);
            4'd12: val7 <= x6;
            default: val7 <= '0;
        endcase
        sh8  <= 44'(val7) << nb;                             // end of t+7
        em8  <= pv[7] && (nb_next >= 7'd32);
        if (pv[8] && em8)                                    // end of t+8
            out_word <= merged[31:0];
    end

    always_ff @(posedge clk) begin
        if (srst || clr) begin
            running   <= 1'b0;
            pv        <= '0;
            out_valid <= 1'b0;
            done      <= 1'b0;
            if (srst) begin
                d_r    <= '0;
                bias_r <= '0;
                slot_r <= '0;
                ic     <= '0;
                ph     <= '0;
                acc    <= '0;
                nb     <= '0;
                cnt    <= '0;
            end
        end else begin
            done      <= 1'b0;
            out_valid <= 1'b0;
            pv        <= {pv[7:0], issue};
            ph        <= {ph[2:0], ic[0]};
            if (issue)
                ic <= ic + 9'd1;
            // t+7: bit position of the value entering t+8.
            if (pv[7])
                nb <= (nb_next >= 7'd32) ? 6'(nb_next - 7'd32) : nb_next[5:0];
            // t+8: merge.
            if (pv[8]) begin
                cnt <= cnt + 9'd1;
                if (em8) begin
                    out_valid <= 1'b1;
                    acc       <= merged >> 32;
                end else begin
                    acc <= merged;
                end
                if (cnt == 9'd255) begin
                    running <= 1'b0;
                    done    <= 1'b1;
                end
            end
            if (start && !running) begin
                running <= 1'b1;
                d_r     <= d;
                bias_r  <= 27'(RECIP_1664 >> d);
                slot_r  <= slot;
                ic      <= '0;
                pv      <= '0;
                acc     <= '0;
                nb      <= '0;
                cnt     <= '0;
            end
        end
    end

endmodule
