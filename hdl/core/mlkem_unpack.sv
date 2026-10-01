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
    output logic        range_err,  // One-cycle pulse per bad coefficient.

    // Byte source.
    input  logic        src_valid,
    input  logic [7:0]  src_byte,
    output logic        src_take,

    // Poly RAM write port.
    output logic        wr_en,
    output logic [9:0] wr_addr,
    output logic [23:0] wr_data
);

    localparam logic [1:0] MODE_SAMPLE = 2'd0;
    localparam logic [1:0] MODE_CBD    = 2'd1;
    localparam logic [1:0] MODE_DECODE = 2'd2;

    localparam logic [11:0] Q = 12'd3329;

    logic        running;
    logic [1:0]  mode_r;
    logic [3:0]  param_r;
    logic        check_r;
    logic [2:0]  slot_r;
    logic [3:0]  w;             // Field width.
    logic [19:0] bitbuf;
    logic [4:0]  nbits;
    logic [8:0]  extracted;     // Fields extracted (CBD/DECODE).
    logic [8:0]  count;         // Coefficients produced.
    logic [11:0] field;         // Stage-2 input.
    logic        field_valid;
    logic [11:0] lo;            // Even coefficient awaiting its pair.

    always_comb begin
        case (mode_r)
            MODE_SAMPLE: w = 4'd12;
            MODE_CBD:    w = 4'(param_r << 1);
            default:     w = param_r;
        endcase
    end

    logic want_field, have_field;
    assign want_field = running && ((mode_r == MODE_SAMPLE) ? (count < 9'd256)
                                                            : (extracted < 9'd256));
    assign have_field = (nbits >= {1'b0, w});
    assign src_take   = want_field && !have_field && src_valid;

    // ---------------------------------------------------------------------
    // Stage 2: field -> coefficient.
    // ---------------------------------------------------------------------
    logic [11:0] coef;
    logic        accept;
    logic        bad;

    function automatic logic [2:0] popcnt3(input logic [2:0] v);
        return {2'b0, v[0]} + {2'b0, v[1]} + {2'b0, v[2]};
    endfunction

    (* use_dsp = "no" *) logic [23:0] dm;   // Decompress: q * field (shift-add)

    always_comb begin
        logic [2:0]  x, y;
        coef   = '0;
        accept = 1'b1;
        bad    = 1'b0;
        x      = '0;
        y      = '0;
        dm     = '0;
        case (mode_r)
            MODE_SAMPLE: begin
                accept = (field < Q);
                coef   = field;
            end
            MODE_CBD: begin
                if (param_r == 4'd3) begin
                    x = popcnt3(field[2:0]);
                    y = popcnt3(field[5:3]);
                end else begin
                    x = popcnt3({1'b0, field[1:0]});
                    y = popcnt3({1'b0, field[3:2]});
                end
                coef = (x >= y) ? {9'b0, x - y} : 12'(Q - {9'b0, y - x});
            end
            default: begin
                if (param_r == 4'd12) begin
                    bad  = (field >= Q);
                    coef = bad ? 12'(field - Q) : field;
                end else begin
                    // Decompress_d(y) = floor((q*y + 2^(d-1)) / 2^d), with
                    // q*y = (y << 11) + (y << 10) + (y << 8) + y in LUTs.
                    dm   = 24'(field) << 11;
                    dm   = dm + (24'(field) << 10) + (24'(field) << 8) + 24'(field);
                    dm   = (dm + (24'd1 << (param_r - 4'd1))) >> param_r;
                    coef = dm[11:0];
                end
            end
        endcase
    end

    assign wr_addr = {slot_r, count[7:1]};
    assign wr_data = {coef, lo};
    assign wr_en   = running && field_valid && accept && count < 9'd256 && count[0];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            running     <= 1'b0;
            mode_r      <= '0;
            param_r     <= '0;
            check_r     <= 1'b0;
            slot_r      <= '0;
            bitbuf      <= '0;
            nbits       <= '0;
            extracted   <= '0;
            count       <= '0;
            field       <= '0;
            field_valid <= 1'b0;
            lo          <= '0;
            done        <= 1'b0;
            range_err   <= 1'b0;
        end else if (clr) begin
            running     <= 1'b0;
            field_valid <= 1'b0;
            done        <= 1'b0;
            range_err   <= 1'b0;
        end else begin
            done      <= 1'b0;
            range_err <= 1'b0;

            if (start && !running) begin
                running     <= 1'b1;
                mode_r      <= mode;
                param_r     <= param;
                check_r     <= check;
                slot_r      <= slot;
                bitbuf      <= '0;
                nbits       <= '0;
                extracted   <= '0;
                count       <= '0;
                field_valid <= 1'b0;
            end else if (running) begin
                // Stage 1: extract a field or pull a byte.
                field_valid <= 1'b0;
                if (want_field && have_field) begin
                    field       <= 12'(bitbuf & ((20'd1 << w) - 20'd1));
                    field_valid <= 1'b1;
                    bitbuf      <= bitbuf >> w;
                    nbits       <= nbits - {1'b0, w};
                    extracted   <= extracted + 9'd1;
                end else if (src_take) begin
                    bitbuf <= bitbuf | (20'(src_byte) << nbits);
                    nbits  <= nbits + 5'd8;
                end

                // Stage 2: accept coefficient.
                if (field_valid && accept && count < 9'd256) begin
                    if (!count[0])
                        lo <= coef;
                    count <= count + 9'd1;
                    if (bad && check_r)
                        range_err <= 1'b1;
                    if (count == 9'd255) begin
                        running <= 1'b0;
                        done    <= 1'b1;
                    end
                end
            end
        end
    end

endmodule
