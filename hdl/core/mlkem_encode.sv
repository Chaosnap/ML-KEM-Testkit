// mlkem_encode.sv - Byte encoding/decoding for ML-KEM polynomials
//
// Implements ByteEncode_d and ByteDecode_d (FIPS 203, Section 4.2.1).
//
// ByteEncode_d packs 256 d-bit coefficients into 32*d bytes.
// ByteDecode_d unpacks 32*d bytes into 256 d-bit coefficients.
//
// This module handles the serialization between the polynomial
// representation (array of coefficients) and the byte-packed format
// used in keys and ciphertexts.
//
// Operates one coefficient at a time in streaming mode.

// ByteEncode: pack d-bit coefficients into a byte stream.
// Outputs bytes sequentially as coefficients are fed in.
module mlkem_byte_encode #(
    parameter int D = 12    // Bits per coefficient (1-12).
) (
    input  logic        clk,
    input  logic        rst_n,

    input  logic [D-1:0] coeff_in,    // Input coefficient.
    input  logic        valid_in,

    output logic [7:0]  byte_out,     // Output byte.
    output logic        valid_out
);

    // Bit accumulator: collects bits from coefficients and outputs bytes.
    logic [D+7:0] bit_buffer;    // Buffer for partial bits.
    logic [4:0]   bit_count;     // Number of valid bits in buffer.

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_buffer <= '0;
            bit_count  <= '0;
            byte_out   <= '0;
            valid_out  <= 1'b0;
        end else begin
            valid_out <= 1'b0;

            if (valid_in) begin
                // Append D bits to the buffer.
                bit_buffer <= bit_buffer | ({{(8){1'b0}}, coeff_in} << bit_count);
                bit_count  <= bit_count + D[4:0];
            end

            // Output a byte whenever we have 8+ bits.
            if (bit_count >= 8 || (valid_in && (bit_count + D[4:0] >= 8))) begin
                byte_out   <= bit_buffer[7:0];
                valid_out  <= 1'b1;
                bit_buffer <= bit_buffer >> 8;
                bit_count  <= bit_count + (valid_in ? D[4:0] : 5'd0) - 5'd8;
            end
        end
    end

endmodule

// ByteDecode: unpack a byte stream into d-bit coefficients.
module mlkem_byte_decode #(
    parameter int D = 12
) (
    input  logic        clk,
    input  logic        rst_n,

    input  logic [7:0]  byte_in,      // Input byte.
    input  logic        valid_in,

    output logic [D-1:0] coeff_out,   // Output coefficient.
    output logic        valid_out
);

    logic [D+7:0] bit_buffer;
    logic [4:0]   bit_count;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_buffer <= '0;
            bit_count  <= '0;
            coeff_out  <= '0;
            valid_out  <= 1'b0;
        end else begin
            valid_out <= 1'b0;

            if (valid_in) begin
                bit_buffer <= bit_buffer | ({{D{1'b0}}, byte_in} << bit_count);
                bit_count  <= bit_count + 5'd8;
            end

            // Output a coefficient whenever we have D+ bits.
            if (bit_count >= D[4:0] || (valid_in && (bit_count + 5'd8 >= D[4:0]))) begin
                coeff_out  <= bit_buffer[D-1:0];
                valid_out  <= 1'b1;
                bit_buffer <= bit_buffer >> D;
                bit_count  <= bit_count + (valid_in ? 5'd8 : 5'd0) - D[4:0];
            end
        end
    end

endmodule
