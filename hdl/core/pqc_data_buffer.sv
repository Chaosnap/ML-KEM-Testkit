// pqc_data_buffer.sv - Dual-port data buffer for PQC accelerator I/O
//
// Shared memory between the host and the PQC datapath. Port A is driven by
// the bus slave (pqc_axi_csr data window, reached over PCIe/AXI/UART);
// port B is driven by the accelerator's sequencer.
//
// Default ML-KEM layout (byte offsets, see scripts/gen_mlkem_ucode.py):
//   0x0000 - 0x17FF  Input region  (DATA_IN_ADDR reset value 0x0000)
//   0x1800 - 0x2FFF  Output region (DATA_OUT_ADDR reset value 0x1800)
//   0x3000 - 0x3FFF  Core scratch  (seeds, hashes, re-encrypted ciphertext)
//
// 16 KB = 4096 x 32-bit words with per-byte write enables; infers a true
// dual-port block RAM (byte-write mode) on Xilinx and Intel devices.

module pqc_data_buffer #(
    parameter int DEPTH      = 4096,        // Number of 32-bit words.
    parameter int ADDR_WIDTH = 12           // log2(DEPTH).
) (
    input  logic                    clk,

    // Port A: host access.
    input  logic                    a_en,
    input  logic [3:0]              a_we,   // Byte write enables.
    input  logic [ADDR_WIDTH-1:0]   a_addr,
    input  logic [31:0]             a_din,
    output logic [31:0]             a_dout,

    // Port B: datapath access.
    input  logic                    b_en,
    input  logic [3:0]              b_we,
    input  logic [ADDR_WIDTH-1:0]   b_addr,
    input  logic [31:0]             b_din,
    output logic [31:0]             b_dout
);

    logic [31:0] mem [0:DEPTH-1];

    always_ff @(posedge clk) begin
        if (a_en) begin
            for (int i = 0; i < 4; i++)
                if (a_we[i])
                    mem[a_addr][8*i +: 8] <= a_din[8*i +: 8];
            a_dout <= mem[a_addr];
        end
    end

    always_ff @(posedge clk) begin
        if (b_en) begin
            for (int i = 0; i < 4; i++)
                if (b_we[i])
                    mem[b_addr][8*i +: 8] <= b_din[8*i +: 8];
            b_dout <= mem[b_addr];
        end
    end

endmodule
