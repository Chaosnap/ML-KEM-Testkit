// mlkem_pack.sv - Polynomial to byte stream for ML-KEM
//
// Reads 256 coefficients from a poly RAM slot and emits
// ByteEncode_d(Compress_d(f)) (FIPS 203 section 4.2.1) one byte at a time.
// For d = 12 the coefficients are encoded without compression.
//
// Compress_d(x) = round(2^d * x / q) mod 2^d
//               = floor(((x << d) + 1664) / 3329) mod 2^d
// The division is an exact multiply-shift: floor(n / 3329) = (n * 161271) >> 29
// for every n < 2^23 (all x < q, d <= 11).

module mlkem_pack (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,

    input  logic        start,
    input  logic [3:0]  d,
    input  logic [3:0]  slot,
    output logic        done,       // One-cycle pulse.

    // Poly RAM read port.
    output logic        rd_en,
    output logic [10:0] rd_addr,
    input  logic [23:0] rd_data,

    // Byte sink (always accepted).
    output logic        out_valid,
    output logic [7:0]  out_byte
);

    localparam logic [17:0] RECIP = 18'd161271;

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
    logic [3:0]  slot_r;
    logic [6:0]  word;
    logic        half;           // 0 = even coefficient, 1 = odd.
    logic [11:0] c0, c1;
    logic [11:0] val;
    logic [19:0] bitbuf;
    logic [4:0]  nbits;

    // Compress_d.
    logic [11:0] x;
    logic [22:0] num;
    logic [40:0] prod;
    logic [11:0] comp;
    always_comb begin
        x    = half ? c1 : c0;
        num  = (23'(x) << d_r) + 23'd1664;
        prod = 41'(num) * 41'(RECIP);
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
