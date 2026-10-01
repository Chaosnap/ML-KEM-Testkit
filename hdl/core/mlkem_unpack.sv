// mlkem_unpack.sv - Byte stream to polynomial for ML-KEM
//
// Converts a byte stream into 256 coefficients written to a poly RAM slot.
// The stream is split into little-endian W-bit fields (FIPS 203 bit order)
// and each field is post-processed according to mode:
//
//   MODE_SAMPLE (W=12): SampleNTT rejection sampling, keep fields < q
//                       (FIPS 203 Algorithm 7; bytes from SHAKE128)
//   MODE_CBD    (W=2*eta): SamplePolyCBD_eta, popcount(lo) - popcount(hi)
//                       (FIPS 203 Algorithm 8; bytes from SHAKE256 PRF)
//   MODE_DECODE (W=d):  ByteDecode_d then Decompress_d (d < 12) or mod q
//                       (d = 12); with check set, fields >= q raise
//                       range_err (FIPS 203 section 7.2 modulus check)
//
// Throughput: the source hands over chunks of up to 8 bytes (one sponge
// lane, or one buffer word); up to two fields are extracted and turned into
// coefficients per cycle, and one RAM word (two coefficients) is written
// per cycle. So SAMPLE / DECODE d=12 consume up to 3 bytes per cycle and
// CBD eta=2 one byte per cycle. With rejection sampling an odd accepted
// coefficient waits in `lo` for its partner.
//
// Pipeline (every stage registered, for timing):
//   S0  source chunk register (cr_*), refilled when empty or used
//   S1  bit buffer: append the chunk if nbits <= 32 (decided from registered
//       state only), extract up to two fields
//   S2  q * field for Decompress (shift-add)
//   S3  field -> coefficient (SampleNTT accept, CBD, Decompress round/shift)
//   S4  pair coefficients, count; S5 write the RAM word

module mlkem_unpack (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,

    input  logic        start,
    input  logic [1:0]  mode,
    input  logic [3:0]  param,      // eta (CBD) or d (DECODE).
    input  logic        check,      // DECODE d=12: modulus check.
    input  logic [2:0]  slot,
    output logic        done,       // One-cycle pulse.
    output logic [1:0]  range_err,  // Per field this cycle: failed modulus check.

    // Byte source: src_n (1..8) bytes in src_data, byte 0 first. src_take
    // consumes the whole chunk.
    input  logic        src_valid,
    input  logic [63:0] src_data,
    input  logic [3:0]  src_n,
    output logic        src_take,

    // Poly RAM write port.
    output logic        wr_en,      // Registered.
    output logic [9:0]  wr_addr,
    output logic [23:0] wr_data
);

    localparam logic [1:0] MODE_SAMPLE = 2'd0;
    localparam logic [1:0] MODE_CBD    = 2'd1;
    localparam logic [1:0] MODE_DECODE = 2'd2;

    localparam logic [11:0] Q  = 12'd3329;
    localparam int          BW = 96;            // 32 bits left + one 64-bit chunk.

    logic        running;
    logic [1:0]  mode_r;
    logic [3:0]  param_r;
    logic        check_r;
    logic [2:0]  slot_r;
    logic [3:0]  w;                 // Field width.
    logic [4:0]  w2;                // 2 * w.
    logic [BW-1:0] bitbuf;
    logic [6:0]  nbits;
    logic [8:0]  extracted;         // Fields extracted (CBD / DECODE).
    logic [11:0] taken_bits;        // Bits taken from the source (CBD / DECODE).
    logic [8:0]  count;             // Coefficients produced (written + lo).
    logic [6:0]  widx;              // Next RAM word.
    logic [11:0] f0, f1;            // S2 inputs.
    logic        fv0, fv1;
    logic [11:0] g0, g1;            // S3 inputs: fields ...
    (* use_dsp = "no" *) logic [23:0] qf0, qf1;   // ... and q * field.
    logic        gv0, gv1;
    logic [13:0] q0, q1;            // S4 inputs: {accept, bad, coef}.
    logic        qv0, qv1;
    logic [11:0] lo;                // Accepted coefficient waiting for its pair.
    logic        have_lo;

    // S0: source chunk register.
    logic        cr_v;
    logic [63:0] cr_d;
    logic [3:0]  cr_n;

    always_comb begin
        case (mode_r)
            MODE_SAMPLE: w = 4'd12;
            MODE_CBD:    w = 4'(param_r << 1);
            default:     w = param_r;
        endcase
        w2 = {w, 1'b0};
    end

    // ---------------------------------------------------------------------
    // S0 / S1: chunk register, bit buffer, field extraction.
    // ---------------------------------------------------------------------
    logic [1:0]    k;               // Fields extracted this cycle.
    logic [5:0]    kw;              // k * w.
    logic          ap;              // Append cr_d this cycle.
    logic          want_bytes;
    logic [BW-1:0] wmask, merged;

    always_comb begin
        logic [1:0] avail, need;
        avail = (nbits >= 7'(w2)) ? 2'd2 : (nbits >= 7'(w)) ? 2'd1 : 2'd0;
        if (!running)
            need = 2'd0;
        else if (mode_r == MODE_SAMPLE)
            need = (count < 9'd256) ? 2'd2 : 2'd0;
        else
            need = (extracted <= 9'd254) ? 2'd2 : (extracted == 9'd255) ? 2'd1 : 2'd0;
        k  = (avail < need) ? avail : need;
        kw = (k == 2'd2) ? 6'(w2) : (k == 2'd1) ? 6'(w) : 6'd0;
        // nbits <= 32 leaves room for a whole chunk before this cycle's fields
        // are removed, so the decision needs no arithmetic on kw.
        ap = running && cr_v && (nbits <= 7'd32);
        wmask  = (BW'(1) << w) - BW'(1);
        merged = bitbuf | (ap ? (BW'(cr_d) << nbits[5:0]) : '0);
        want_bytes = running && ((mode_r == MODE_SAMPLE) ? (count < 9'd256)
                                                        : (taken_bits < {w, 8'd0}));
    end

    assign src_take = want_bytes && src_valid && (!cr_v || ap);

    // ---------------------------------------------------------------------
    // Stage 2: field -> coefficient (two lanes).
    // ---------------------------------------------------------------------
    function automatic logic [2:0] popcnt3(input logic [2:0] v);
        return {2'b0, v[0]} + {2'b0, v[1]} + {2'b0, v[2]};
    endfunction

    // q * y as shift-add (S2).
    function automatic logic [23:0] q_times(input logic [11:0] y);
        return ((24'(y) << 11) + (24'(y) << 10)) + ((24'(y) << 8) + 24'(y));
    endfunction

    // Decompress_d(y) = floor((q*y + 2^(d-1)) / 2^d), from qy = q*y (S3).
    function automatic logic [11:0] decompress(input logic [23:0] qy, input logic [3:0] d);
        logic [23:0] t;
        t = (qy + (24'd1 << (d - 4'd1))) >> d;
        return t[11:0];
    endfunction

    // {accept, bad, coef} for one field.
    function automatic logic [13:0] proc(input logic [11:0] f, input logic [23:0] qf,
                                         input logic [1:0] m, input logic [3:0] p);
        logic [2:0] x, y;
        case (m)
            MODE_SAMPLE: return {(f < Q), 1'b0, f};
            MODE_CBD: begin
                if (p == 4'd3) begin
                    x = popcnt3(f[2:0]);
                    y = popcnt3(f[5:3]);
                end else begin
                    x = popcnt3({1'b0, f[1:0]});
                    y = popcnt3({1'b0, f[3:2]});
                end
                return {1'b1, 1'b0, (x >= y) ? {9'b0, x - y} : 12'(Q - {9'b0, y - x})};
            end
            default: begin
                if (p == 4'd12)
                    return {1'b1, (f >= Q), (f >= Q) ? 12'(f - Q) : f};
                return {1'b1, 1'b0, decompress(qf, p)};
            end
        endcase
    endfunction

    (* use_dsp = "no" *) logic [13:0] r0, r1;
    assign r0 = proc(g0, qf0, mode_r, param_r);
    assign r1 = proc(g1, qf1, mode_r, param_r);

    // ---------------------------------------------------------------------
    // S3: accepted coefficients this cycle, limited to the 256 still needed.
    // ---------------------------------------------------------------------
    logic        a0, a1;
    logic [1:0]  nacc, total;
    logic [11:0] l0, l1, l2;        // [lo] ++ accepted, in order.
    always_comb begin
        a0 = qv0 && q0[13];
        a1 = qv1 && q1[13];
        if (count == 9'd255 && a0)
            a1 = 1'b0;
        if (count >= 9'd256) begin
            a0 = 1'b0;
            a1 = 1'b0;
        end
        nacc  = 2'(a0) + 2'(a1);
        total = 2'(have_lo) + nacc;
        // Compact [lo, c0, c1] into l0, l1, l2.
        l0 = have_lo ? lo : (a0 ? q0[11:0] : q1[11:0]);
        if (have_lo)
            l1 = a0 ? q0[11:0] : q1[11:0];
        else
            l1 = q1[11:0];
        l2 = q1[11:0];
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            running    <= 1'b0;
            mode_r     <= '0;
            param_r    <= '0;
            check_r    <= 1'b0;
            slot_r     <= '0;
            nbits      <= '0;
            extracted  <= '0;
            taken_bits <= '0;
            count      <= '0;
            widx       <= '0;
            fv0        <= 1'b0;
            fv1        <= 1'b0;
            gv0        <= 1'b0;
            gv1        <= 1'b0;
            qv0        <= 1'b0;
            qv1        <= 1'b0;
            have_lo    <= 1'b0;
            cr_v       <= 1'b0;
            wr_en      <= 1'b0;
            done       <= 1'b0;
            range_err  <= '0;
        end else if (clr) begin
            running   <= 1'b0;
            fv0       <= 1'b0;
            fv1       <= 1'b0;
            gv0       <= 1'b0;
            gv1       <= 1'b0;
            qv0       <= 1'b0;
            qv1       <= 1'b0;
            cr_v      <= 1'b0;
            wr_en     <= 1'b0;
            done      <= 1'b0;
            range_err <= '0;
        end else begin
            done      <= 1'b0;
            range_err <= '0;
            wr_en     <= 1'b0;

            if (start && !running) begin
                running    <= 1'b1;
                mode_r     <= mode;
                param_r    <= param;
                check_r    <= check;
                slot_r     <= slot;
                nbits      <= '0;
                extracted  <= '0;
                taken_bits <= '0;
                count      <= '0;
                widx       <= '0;
                fv0        <= 1'b0;
                fv1        <= 1'b0;
                gv0        <= 1'b0;
                gv1        <= 1'b0;
                qv0        <= 1'b0;
                qv1        <= 1'b0;
                have_lo    <= 1'b0;
                cr_v       <= 1'b0;
            end else if (running) begin
                // S0.
                if (src_take) begin
                    cr_v       <= 1'b1;
                    taken_bits <= taken_bits + 12'({src_n, 3'b000});
                end else if (ap) begin
                    cr_v <= 1'b0;
                end
                // S1.
                nbits     <= nbits + (ap ? 7'({cr_n, 3'b000}) : 7'd0) - 7'(kw);
                extracted <= extracted + 9'(k);
                fv0       <= (k != 2'd0);
                fv1       <= (k == 2'd2);
                // S2.
                gv0 <= fv0;
                gv1 <= fv1;
                qv0 <= gv0;
                qv1 <= gv1;
                if (check_r)
                    range_err <= {gv1 && r1[12], gv0 && r0[12]};
                // S3 -> S4.
                if (total >= 2'd2) begin
                    wr_en   <= 1'b1;
                    widx    <= widx + 7'd1;
                    have_lo <= (total == 2'd3);
                end else begin
                    have_lo <= (total == 2'd1);
                end
                count <= count + 9'(nacc);
                if (count + 9'(nacc) == 9'd256) begin
                    running <= 1'b0;
                    done    <= 1'b1;
                end
            end
        end
    end

    // Data registers (no reset needed).
    always_ff @(posedge clk) begin
        if (src_take) begin
            cr_d <= (src_n >= 4'd8) ? src_data
                                    : (src_data & ((64'd1 << {src_n, 3'b000}) - 64'd1));
            cr_n <= src_n;
        end
        // Shifts by w and 2w depend only on registered w; k picks one.
        if (start && !running)
            bitbuf <= '0;
        else if (k == 2'd2)
            bitbuf <= merged >> w2;
        else if (k == 2'd1)
            bitbuf <= merged >> w;
        else
            bitbuf <= merged;
        f0 <= 12'(bitbuf & wmask);
        f1 <= 12'((bitbuf >> w) & wmask);
        g0  <= f0;
        g1  <= f1;
        qf0 <= q_times(f0);
        qf1 <= q_times(f1);
        q0  <= r0;
        q1  <= r1;
        lo <= (total >= 2'd2) ? l2 : l0;
        wr_addr <= {slot_r, widx};
        wr_data <= {l1, l0};
    end

endmodule
