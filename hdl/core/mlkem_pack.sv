// mlkem_pack.sv - Polynomial to byte stream for ML-KEM
//
// Reads 256 coefficients from a poly RAM slot and emits
// ByteEncode_d(Compress_d(f)) (FIPS 203 section 4.2.1) one byte at a time.
// For d = 12 the coefficients are encoded without compression.
//
// Compress_d(x) = round(2^d * x / q) mod 2^d
//               = floor(((x << d) + 1664) / 3329) mod 2^d
// The division is an exact multiply-shift: floor(n / 3329) = (n * 161271) >> 29
// for every n < 2^23 (all x < q, d <= 11). With n = (x << d) + 1664:
//   n * 161271 = ((x * 161271) << d) + 1664 * 161271
// x * 161271 is a constant multiply done with shifts and adds in LUTs (CSD
// 161271 = 2^17 + 2^15 - 2^11 - 2^9 - 2^3 - 1) for both coefficients of a
// word while it is latched, so no DSP is used and the variable shift by d
// stays out of the adder tree.

module mlkem_pack (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,

    input  logic        start,
    input  logic [3:0]  d,
    input  logic [2:0]  slot,
    output logic        done,       // One-cycle pulse.

    // Poly RAM read port.
    output logic        rd_en,
    output logic [9:0] rd_addr,
    input  logic [23:0] rd_data,

    // Byte sink (always accepted).
    output logic        out_valid,
    output logic [7:0]  out_byte
);

    localparam logic [40:0] RECIP_1664 = 41'd268354944;   // 1664 * 161271

    // x * 161271 for x < q (< 2^30).
    function automatic logic [29:0] mul_recip(input logic [11:0] v);
        logic [29:0] e;
        e = 30'(v);
        return (e << 17) + (e << 15) - (e << 11) - (e << 9) - (e << 3) - e;
    endfunction

    typedef enum logic [2:0] {
        S_IDLE,
        S_READ,     // Issue word read.
        S_LATCH,    // Capture coefficient pair.
        S_COMP,     // Compress one coefficient (registered).
        S_PUSH,     // Append d bits.
        S_EMIT      // Emit whole bytes.
    } state_t;

    state_t      state;
    logic [3:0]  d_r;
    logic [2:0]  slot_r;
    logic [6:0]  word;
    logic        half;           // 0 = even coefficient, 1 = odd.
    logic [11:0] c0, c1;
    (* use_dsp = "no" *) logic [29:0] xr0, xr1;   // c0 * 161271, c1 * 161271
    logic [11:0] val;
    logic [19:0] bitbuf;
    logic [4:0]  nbits;

    // Compress_d.
    logic [11:0] x;
    logic [40:0] prod;
    logic [11:0] comp;
    always_comb begin
        x    = half ? c1 : c0;
        prod = (41'(half ? xr1 : xr0) << d_r) + RECIP_1664;
        comp = (d_r == 4'd12) ? x : 12'((prod >> 29) & ((41'd1 << d_r) - 41'd1));
    end

    assign rd_en    = (state == S_READ);
    assign rd_addr  = {slot_r, word};
    assign out_byte = bitbuf[7:0];
    assign out_valid = (state == S_EMIT) && (nbits >= 5'd8);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state  <= S_IDLE;
            d_r    <= '0;
            slot_r <= '0;
            word   <= '0;
            half   <= 1'b0;
            c0     <= '0;
            c1     <= '0;
            xr0    <= '0;
            xr1    <= '0;
            val    <= '0;
            bitbuf <= '0;
            nbits  <= '0;
            done   <= 1'b0;
        end else if (clr) begin
            state <= S_IDLE;
            done  <= 1'b0;
        end else begin
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    if (start) begin
                        d_r    <= d;
                        slot_r <= slot;
                        word   <= '0;
                        bitbuf <= '0;
                        nbits  <= '0;
                        state  <= S_READ;
                    end
                end
                S_READ: state <= S_LATCH;
                S_LATCH: begin
                    c0    <= rd_data[11:0];
                    c1    <= rd_data[23:12];
                    xr0   <= mul_recip(rd_data[11:0]);
                    xr1   <= mul_recip(rd_data[23:12]);
                    half  <= 1'b0;
                    state <= S_COMP;
                end
                S_COMP: begin
                    val   <= comp;
                    state <= S_PUSH;
                end
                S_PUSH: begin
                    bitbuf <= bitbuf | (20'(val) << nbits);
                    nbits  <= nbits + {1'b0, d_r};
                    state  <= S_EMIT;
                end
                S_EMIT: begin
                    if (nbits >= 5'd8) begin
                        bitbuf <= bitbuf >> 8;
                        nbits  <= nbits - 5'd8;
                    end else if (!half) begin
                        half  <= 1'b1;
                        state <= S_COMP;
                    end else if (word == 7'd127) begin
                        state <= S_IDLE;
                        done  <= 1'b1;
                    end else begin
                        word  <= word + 7'd1;
                        state <= S_READ;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
