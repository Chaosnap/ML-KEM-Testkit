// pqc_data_buffer.sv - Dual-port data buffer for PQC accelerator I/O
//
// Shared memory between the host and the PQC datapath. Port A is driven by
// the bus slave (pqc_axi_csr data window, reached over PCIe/AXI/UART);
// port B is driven by the accelerator's sequencer.
//
// ML-KEM-768 layout (byte offsets, see scripts/gen_mlkem_ucode.py):
//   0x0000 - 0x0DFF  Input region  (DATA_IN_ADDR reset value 0x0000)
//   0x0E00 - 0x1BFF  Output region (DATA_OUT_ADDR reset value 0x0E00);
//                    Decaps keeps the re-encrypted c' at OUT + 0x100
//   0x1C00 - 0x1CFF  Core scratch  (seeds, hashes, m')
//   0x1D00 - 0x1FFF  unused
//
// 8 KB = 2048 x 32-bit words with per-byte write enables; infers a true
// dual-port block RAM (byte-write mode, 2 x RAMB36) on Xilinx devices.

module pqc_data_buffer #(
    parameter int DEPTH      = 2048,        // Number of 32-bit words.
    parameter int ADDR_WIDTH = 11           // log2(DEPTH).
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
