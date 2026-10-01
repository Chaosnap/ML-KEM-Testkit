// mlkem_pack.sv - Polynomial to byte stream for ML-KEM
//
// Reads 256 coefficients from a poly RAM slot and emits
// ByteEncode_d(Compress_d(f)) (FIPS 203 section 4.2.1) as 32-bit
// little-endian words. For d = 12 the coefficients are encoded without
// compression. 256 * d is a multiple of 32, so the stream ends on a word.
//
// Compress_d(x) = round(2^d * x / q) mod 2^d
//               = floor(((x << d) + 1664) / 3329) mod 2^d
// The division is an exact multiply-shift: floor(n / 3329) = (n * 161271) >> 29
// for every n < 2^23 (all x < q, d <= 11). With n = (x << d) + 1664:
//   n * 161271 = ((x * 161271) << d) + 1664 * 161271
// x * 161271 is a constant multiply done with shifts and adds in LUTs (CSD
// 161271 = 2^17 + 2^15 - 2^11 - 2^9 - 2^3 - 1), so no DSP is used.
//
// Pipeline, one coefficient per cycle (c = coefficient issued at cycle t):
//   t    read word c/2 (even c only)     t+3  Compress_d -> val
//   t+1  register the RAM word           t+4  append d bits, emit a word
//   t+2  x * 161271                           when 32 bits are available

module mlkem_pack (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,

    input  logic        start,
    input  logic [3:0]  d,
    input  logic [2:0]  slot,
    output logic        done,       // One-cycle pulse (with the last word).

    // Poly RAM read port.
    output logic        rd_en,
    output logic [9:0]  rd_addr,
    input  logic [23:0] rd_data,

    // Word sink (always accepted).
    output logic        out_valid,
    output logic [31:0] out_word
);

    localparam logic [40:0] RECIP_1664 = 41'd268354944;   // 1664 * 161271

    // x * 161271 for x < q (< 2^30).
    function automatic logic [29:0] mul_recip(input logic [11:0] v);
        logic [29:0] e;
        e = 30'(v);
        return ((e << 17) + (e << 15)) - ((e << 11) + (e << 9)) - ((e << 3) + e);
    endfunction

    logic        running;
    logic [3:0]  d_r;
    logic [2:0]  slot_r;
    logic [8:0]  ic;                // Next coefficient to issue (0..256).
    logic        issue;
    assign issue   = running && !ic[8];
    assign rd_en   = issue && !ic[0];
    assign rd_addr = {slot_r, ic[7:1]};

    // Pipeline valid / half bits: [0] = t+1, [1] = t+2, [2] = t+3, [3] = t+4.
    logic [3:0]  pv;
    logic [1:0]  ph;                // Coefficient half (odd c) at t+1, t+2.
    logic [23:0] rw;                // t+2: RAM word.
    logic [11:0] x3;                // t+3: coefficient.
    (* use_dsp = "no" *) logic [29:0] xr3;   // t+3: x * 161271.
    logic [11:0] val4;              // t+4: compressed value.
    logic [43:0] acc;               // Pending bits.
    logic [5:0]  nb;                // Number of pending bits (< 32).
    logic [8:0]  cnt;               // Coefficients appended.

    // Compress_d at t+3.
    logic [40:0] prod;
    logic [11:0] comp;
    always_comb begin
        prod = (41'(xr3) << d_r) + RECIP_1664;
        comp = (d_r == 4'd12) ? x3 : 12'((prod >> 29) & ((41'd1 << d_r) - 41'd1));
    end

    // Bit accumulator at t+4.
    logic [43:0] merged;
    logic [6:0]  nb_next;
    assign merged  = acc | (44'(val4) << nb);
    assign nb_next = 7'(nb) + 7'(d_r);

    always_ff @(posedge clk) begin
        rw   <= rd_data;
        x3   <= ph[1] ? rw[23:12] : rw[11:0];
        xr3  <= mul_recip(ph[1] ? rw[23:12] : rw[11:0]);
        val4 <= comp;
        if (pv[3] && nb_next >= 7'd32)
            out_word <= merged[31:0];
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            running   <= 1'b0;
            d_r       <= '0;
            slot_r    <= '0;
            ic        <= '0;
            pv        <= '0;
            ph        <= '0;
            acc       <= '0;
            nb        <= '0;
            cnt       <= '0;
            out_valid <= 1'b0;
            done      <= 1'b0;
        end else if (clr) begin
            running   <= 1'b0;
            pv        <= '0;
            out_valid <= 1'b0;
            done      <= 1'b0;
        end else begin
            done      <= 1'b0;
            out_valid <= 1'b0;
            pv        <= {pv[2:0], issue};
            ph        <= {ph[0], ic[0]};
            if (issue)
                ic <= ic + 9'd1;
            if (pv[3]) begin
                cnt <= cnt + 9'd1;
                if (nb_next >= 7'd32) begin
                    out_valid <= 1'b1;
                    acc       <= merged >> 32;
                    nb        <= 6'(nb_next - 7'd32);
                end else begin
                    acc <= merged;
                    nb  <= nb_next[5:0];
                end
                if (cnt == 9'd255) begin
                    running <= 1'b0;
                    done    <= 1'b1;
                end
            end
            if (start && !running) begin
                running <= 1'b1;
                d_r     <= d;
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
