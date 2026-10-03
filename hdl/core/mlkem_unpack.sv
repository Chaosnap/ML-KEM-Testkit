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
// The source delivers whole 64-bit chunks (a sponge lane, or two buffer
// words; every ML-KEM-768 input is a multiple of 8 bytes). Up to two
// fields are extracted per cycle and one RAM word (two coefficients) is
// written per cycle, so SAMPLE / DECODE d=12 consume up to 3 bytes per
// cycle. With rejection sampling an odd accepted coefficient waits in `lo`.
//
// Pipeline (all registered, no variable shifter in any feedback loop):
//   S1  3-slot chunk queue {q2,q1,q0} and bit pointer p into q0; p + w and
//       p + 2w are kept in registers; fields are read from the registered
//       window {q1,q0} at p and p + w
//   S1b byte selection is registered before the remaining 0..7 bit shift;
//       one extra cycle of latency, still up to two fields per cycle
//   S2  q * field for Decompress (shift-add); SampleNTT accept, CBD, d=12
//   S3  Decompress rounding add
//   S4  Decompress shift, select the mode's result
//   S5  pair coefficients, count; S6 write the RAM word

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

    // Chunk source: 8 bytes in src_data, byte 0 first; src_take consumes it.
    input  logic        src_valid,
    input  logic [63:0] src_data,
    output logic        src_take,

    // Poly RAM write port.
    output logic        wr_en,      // Registered.
    output logic [9:0]  wr_addr,
    output logic [23:0] wr_data
);

    // Registered active-high synchronous reset, local to this module
    // (replicated by fanout; no inverter on the high-fanout net).
    (* max_fanout = 32 *) logic srst;
    always_ff @(posedge clk)
        srst <= !rst_n;

    localparam logic [1:0] MODE_SAMPLE = 2'd0;
    localparam logic [1:0] MODE_CBD    = 2'd1;

    localparam logic [11:0] Q = 12'd3329;

    function automatic logic [3:0] width(input logic [1:0] m, input logic [3:0] p);
        case (m)
            MODE_SAMPLE: return 4'd12;
            MODE_CBD:    return 4'(p << 1);
            default:     return p;
        endcase
    endfunction

    logic        running;
    logic [1:0]  mode_r;
    logic [3:0]  param_r;
    logic        check_r;
    logic [2:0]  slot_r;
    logic [3:0]  w;                 // Field width.
    logic [11:0] wmask;             // (1 << w) - 1.
    logic [6:0]  need_chunks;       // CBD / DECODE: 256 * w / 64 = 4w.
    logic [6:0]  taken;             // Chunks taken from the source.
    logic        want_q;            // Take more chunks (registered).
    logic [8:0]  extracted;         // Fields extracted (CBD / DECODE).
    logic [1:0]  need_q;            // Fields wanted this cycle (registered).
    logic [8:0]  count;             // Coefficients produced (written + lo).
    logic [6:0]  widx;              // Next RAM word.

    // S1 state.
    logic [63:0] q0, q1, q2;
    logic        v0, v1, v2;
    logic [6:0]  p, pw1, pw2;       // p (< 64), p + w, p + 2w.
    logic [6:0]  lim1, lim2;        // 64 - w, 64 - 2w.
    logic        ge1, gt1, ge2, gt2;   // p >= / > lim1, lim2 (registered with p).
    logic [18:0] bytewin0, bytewin1; // 12 field bits + up to 7 alignment bits.
    logic [2:0] bitpos0, bitpos1;
    logic bv0, bv1;
    logic [11:0] f0, f1;            // S2 inputs.
    logic        fv0, fv1;
    (* use_dsp = "no" *) logic [23:0] qf0, qf1;   // S3 inputs: q * field ...
    logic [13:0] alt0, alt1;        // ... and {accept, bad, coef} of non-Decompress modes.
    logic        gv0, gv1;
    logic [23:0] rn0, rn1;          // S4 inputs: q * field + 2^(d-1) ...
    logic [13:0] alt0b, alt1b;
    logic        hv0, hv1;
    logic        dcmp;              // DECODE with d < 12 (Decompress).
    logic [13:0] rq0, rq1;          // S5 inputs: {accept, bad, coef}.
    logic        qv0, qv1;
    logic [11:0] lo;                // Accepted coefficient waiting for its pair.
    logic        have_lo;

    assign src_take = running && want_q && src_valid && !v2;

    // ---------------------------------------------------------------------
    // S1: how many fields this cycle, pointer and queue update.
    // ---------------------------------------------------------------------
    logic [1:0] k;
    logic [6:0] p_next;
    logic       shift;              // q0 used up.
    always_comb begin
        logic e1, e2;
        // A field ending at or before bit 64 (p <= 64 - w) lies in q0;
        // otherwise q1 is needed. Ending at bit 64 or later uses up q0.
        e1 = v0 && (v1 || !gt1);
        e2 = v0 && (v1 || !gt2);
        if (need_q == 2'd2 && e2)
            k = 2'd2;
        else if (need_q != 2'd0 && e1)
            k = 2'd1;
        else
            k = 2'd0;
        p_next = (k == 2'd2) ? pw2 : (k == 2'd1) ? pw1 : p;
        shift  = (k == 2'd2) ? ge2 : (k == 2'd1) ? ge1 : 1'b0;
    end

    // Window {q1, q0} read at p and p + w (registered positions).
    logic [127:0] win;
    assign win = {q1, q0};

    always_ff @(posedge clk) begin
        // Split the wide barrel selector from fine alignment and masking.
        bytewin0 <= 19'(win >> {p[6:3], 3'b000});
        bytewin1 <= 19'(win >> {pw1[6:3], 3'b000});
        bitpos0 <= p[2:0];
        bitpos1 <= pw1[2:0];
        f0 <= 12'(bytewin0 >> bitpos0) & wmask;
        f1 <= 12'(bytewin1 >> bitpos1) & wmask;
    end

    // ---------------------------------------------------------------------
    // S2 / S3: field -> coefficient (two lanes).
    // ---------------------------------------------------------------------
    function automatic logic [2:0] popcnt3(input logic [2:0] v);
        return {2'b0, v[0]} + {2'b0, v[1]} + {2'b0, v[2]};
    endfunction

    // q * y as shift-add (S2).
    function automatic logic [23:0] q_times(input logic [11:0] y);
        return ((24'(y) << 11) + (24'(y) << 10)) + ((24'(y) << 8) + 24'(y));
    endfunction

    // {accept, bad, coef} for one field in SAMPLE, CBD and DECODE d = 12
    // (Decompress, d < 12, is done in S3 / S4 from q * field).
    function automatic logic [13:0] proc(input logic [11:0] f, input logic [1:0] m,
                                         input logic [3:0] pr);
        logic [2:0] x, y;
        case (m)
            MODE_SAMPLE: return {(f < Q), 1'b0, f};
            MODE_CBD: begin
                if (pr == 4'd3) begin
                    x = popcnt3(f[2:0]);
                    y = popcnt3(f[5:3]);
                end else begin
                    x = popcnt3({1'b0, f[1:0]});
                    y = popcnt3({1'b0, f[3:2]});
                end
                return {1'b1, 1'b0, (x >= y) ? {9'b0, x - y} : 12'(Q - {9'b0, y - x})};
            end
            default:
                return {1'b1, (f >= Q), (f >= Q) ? 12'(f - Q) : f};
        endcase
    endfunction

    // S4: Decompress_d(y) = floor((q*y + 2^(d-1)) / 2^d) = rn >> d.
    logic [13:0] r0, r1;
    assign r0 = dcmp ? {2'b10, 12'(rn0 >> param_r)} : alt0b;
    assign r1 = dcmp ? {2'b10, 12'(rn1 >> param_r)} : alt1b;

    // ---------------------------------------------------------------------
    // S4: accepted coefficients this cycle, limited to the 256 still needed.
    // ---------------------------------------------------------------------
    logic        a0, a1;
    logic [1:0]  nacc, total;
    logic [11:0] l0, l1, l2;        // [lo] ++ accepted, in order.
    always_comb begin
        a0 = qv0 && rq0[13];
        a1 = qv1 && rq1[13];
        if (count == 9'd255 && a0)
            a1 = 1'b0;
        if (count >= 9'd256) begin
            a0 = 1'b0;
            a1 = 1'b0;
        end
        nacc  = 2'(a0) + 2'(a1);
        total = 2'(have_lo) + nacc;
        l0 = have_lo ? lo : (a0 ? rq0[11:0] : rq1[11:0]);
        if (have_lo)
            l1 = a0 ? rq0[11:0] : rq1[11:0];
        else
            l1 = rq1[11:0];
        l2 = rq1[11:0];
    end

    // ---------------------------------------------------------------------
    // Control.
    // ---------------------------------------------------------------------
    always_ff @(posedge clk) begin                 // Synchronous reset.
        if (srst || clr) begin
            running   <= 1'b0;
            v0        <= 1'b0;
            v1        <= 1'b0;
            v2        <= 1'b0;
            want_q    <= 1'b0;
            need_q    <= 2'd0;
            bv0       <= 1'b0;
            bv1       <= 1'b0;
            fv0       <= 1'b0;
            fv1       <= 1'b0;
            gv0       <= 1'b0;
            gv1       <= 1'b0;
            hv0       <= 1'b0;
            hv1       <= 1'b0;
            qv0       <= 1'b0;
            qv1       <= 1'b0;
            wr_en     <= 1'b0;
            done      <= 1'b0;
            range_err <= '0;
            if (srst) begin
                mode_r      <= '0;
                param_r     <= '0;
                check_r     <= 1'b0;
                slot_r      <= '0;
                w           <= 4'd12;
                wmask       <= 12'hFFF;
                need_chunks <= '0;
                taken       <= '0;
                extracted   <= '0;
                count       <= '0;
                widx        <= '0;
                have_lo     <= 1'b0;
                p           <= '0;
                pw1         <= '0;
                pw2         <= '0;
                lim1        <= 7'd52;
                lim2        <= 7'd40;
                ge1         <= 1'b0;
                gt1         <= 1'b0;
                ge2         <= 1'b0;
                gt2         <= 1'b0;
                dcmp        <= 1'b0;
            end
        end else begin
            done      <= 1'b0;
            range_err <= '0;
            wr_en     <= 1'b0;

            if (start && !running) begin
                logic [3:0] wn;
                wn = width(mode, param);
                running     <= 1'b1;
                mode_r      <= mode;
                param_r     <= param;
                check_r     <= check;
                slot_r      <= slot;
                w           <= wn;
                dcmp        <= (mode != MODE_SAMPLE) && (mode != MODE_CBD) && (param != 4'd12);
                wmask       <= 12'((13'd1 << wn) - 13'd1);
                need_chunks <= 7'({wn, 2'b00});
                taken       <= '0;
                want_q      <= 1'b1;
                need_q      <= 2'd2;
                extracted   <= '0;
                count       <= '0;
                widx        <= '0;
                have_lo     <= 1'b0;
                v0          <= 1'b0;
                v1          <= 1'b0;
                v2          <= 1'b0;
                p           <= '0;
                pw1         <= 7'(wn);
                pw2         <= 7'({wn, 1'b0});
                lim1        <= 7'd64 - 7'(wn);
                lim2        <= 7'd64 - 7'({wn, 1'b0});
                ge1         <= 1'b0;            // p = 0 < 64 - w
                gt1         <= 1'b0;
                ge2         <= 1'b0;
                gt2         <= 1'b0;
                bv0         <= 1'b0;
                bv1         <= 1'b0;
                fv0         <= 1'b0;
                fv1         <= 1'b0;
                gv0         <= 1'b0;
                gv1         <= 1'b0;
                hv0         <= 1'b0;
                hv1         <= 1'b0;
                qv0         <= 1'b0;
                qv1         <= 1'b0;
            end else if (running) begin
                logic [6:0] p_n;
                logic [8:0] ext_n;
                // S1: queue. Pop q0 when used up, push the taken chunk behind
                // the remaining valid slots.
                if (shift) begin
                    q0 <= q1; v0 <= v1;
                    q1 <= q2; v1 <= v2;
                    v2 <= 1'b0;
                end
                if (src_take) begin
                    taken <= taken + 7'd1;
                    case ({shift, v1, v0})
                        3'b000: begin q0 <= src_data; v0 <= 1'b1; end
                        3'b001: begin q1 <= src_data; v1 <= 1'b1; end
                        3'b011: begin q2 <= src_data; v2 <= 1'b1; end
                        3'b101: begin q0 <= src_data; v0 <= 1'b1; end   // q1 was empty
                        3'b111: begin q1 <= src_data; v1 <= 1'b1; end
                        default: ;
                    endcase
                end
                // SAMPLE does not know how many bytes it needs: keep taking
                // and extracting until 256 coefficients are accepted (extra
                // fields in flight are dropped at S4).
                if (mode_r == MODE_SAMPLE)
                    want_q <= (count < 9'd256);
                else
                    want_q <= (taken + 7'(src_take) < need_chunks);
                p_n = {1'b0, p_next[5:0]};
                p   <= p_n;
                pw1 <= p_n + 7'(w);
                pw2 <= p_n + 7'({w, 1'b0});
                ge1 <= (p_n >= lim1);
                gt1 <= (p_n >  lim1);
                ge2 <= (p_n >= lim2);
                gt2 <= (p_n >  lim2);
                ext_n = extracted + 9'(k);
                extracted <= ext_n;
                if (mode_r == MODE_SAMPLE)
                    need_q <= (count < 9'd256) ? 2'd2 : 2'd0;
                else
                    need_q <= (ext_n <= 9'd254) ? 2'd2 : (ext_n == 9'd255) ? 2'd1 : 2'd0;
                bv0 <= (k != 2'd0);
                bv1 <= (k == 2'd2);
                fv0 <= bv0;
                fv1 <= bv1;
                // S2 .. S4.
                gv0 <= fv0;
                gv1 <= fv1;
                hv0 <= gv0;
                hv1 <= gv1;
                qv0 <= hv0;
                qv1 <= hv1;
                if (check_r)
                    range_err <= {gv1 && alt1[12], gv0 && alt0[12]};
                // S4 -> S5.
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
                    want_q  <= 1'b0;
                    done    <= 1'b1;
                end
            end
        end
    end

    // Data registers (no reset needed).
    always_ff @(posedge clk) begin
        // S2.
        qf0  <= q_times(f0);
        qf1  <= q_times(f1);
        alt0 <= proc(f0, mode_r, param_r);
        alt1 <= proc(f1, mode_r, param_r);
        // S3.
        rn0   <= qf0 + (24'd1 << (param_r - 4'd1));
        rn1   <= qf1 + (24'd1 << (param_r - 4'd1));
        alt0b <= alt0;
        alt1b <= alt1;
        // S4.
        rq0 <= r0;
        rq1 <= r1;
        lo  <= (total >= 2'd2) ? l2 : l0;
        wr_addr <= {slot_r, widx};
        wr_data <= {l1, l0};
    end

endmodule
