// pqc_data_buffer.sv - Dual-port data buffer for PQC accelerator I/O
//
// Provides a shared memory region for host-to-FPGA and FPGA-to-host
// data transfer. Port A is connected to the external bus (AXI/UART/PCIe)
// for host access. Port B is connected to the PQC datapath for internal
// access during operations.
//
// Memory layout (configurable via parameters):
//   0x0000 - 0x0FFF  Input region  (keys, messages, ciphertexts from host)
//   0x1000 - 0x1FFF  Output region (keys, signatures, shared secrets to host)
//
// Size: 8 KB default (sufficient for ML-KEM-1024 which has the largest
// combined key+ciphertext at ~3.2 KB)

module pqc_data_buffer #(
    parameter int DEPTH      = 2048,        // Number of 32-bit words.
    parameter int ADDR_WIDTH = 11           // log2(DEPTH).
) (
    input  logic                    clk,

    // Port A: Host access (read/write from bus).
    input  logic                    a_en,
    input  logic                    a_we,
    input  logic [ADDR_WIDTH-1:0]   a_addr,
    input  logic [31:0]             a_din,
    output logic [31:0]             a_dout,

    // Port B: Datapath access (read/write from PQC core).
    input  logic                    b_en,
    input  logic                    b_we,
    input  logic [ADDR_WIDTH-1:0]   b_addr,
    input  logic [31:0]             b_din,
    output logic [31:0]             b_dout
);

    // True dual-port RAM. Infers BRAM on both Xilinx and Intel.
    logic [31:0] mem [0:DEPTH-1];

    // Port A.
    always_ff @(posedge clk) begin
        if (a_en) begin
            if (a_we)
                mem[a_addr] <= a_din;
            a_dout <= mem[a_addr];
        end
    end

    // Port B.
    always_ff @(posedge clk) begin
        if (b_en) begin
            if (b_we)
                mem[b_addr] <= b_din;
            b_dout <= mem[b_addr];
        end
    end

endmodule
