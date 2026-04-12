// pqc_axi_csr.sv - AXI-Lite CSR slave for PQC accelerator cores
//
// Implements the standard pqc-testkit register map as an AXI4-Lite slave.
// This module is shared by all PQC cores (ML-KEM, ML-DSA, SLH-DSA).
// It handles bus protocol and register read/write; the actual PQC datapath
// connects via the internal register ports.
//
// Register map (matches pkg/fpga/device.go):
//   0x00 CTRL          RW  start | reset
//   0x04 STATUS        RO  busy | done | error
//   0x08 ALG_ID        RO  algorithm identifier
//   0x0C SEC_LEVEL     RW  security level
//   0x10 OP_MODE       RW  operation mode
//   0x14 CYCLE_COUNT   RO  cycle counter
//   0x18 VERSION       RO  core version
//   0x1C ERROR_CODE    RO  error code
//   0x20 DATA_IN_ADDR  RW  input buffer offset
//   0x24 DATA_IN_LEN   RW  input data length
//   0x28 DATA_OUT_ADDR RW  output buffer offset
//   0x2C DATA_OUT_LEN  RO  output data length

module pqc_axi_csr #(
    parameter int ALG_ID       = 1,         // 1=ML-KEM, 2=ML-DSA, 3=SLH-DSA.
    parameter int VERSION_MAJ  = 1,
    parameter int VERSION_MIN  = 0,
    parameter int VERSION_PAT  = 0,
    parameter int ADDR_WIDTH   = 6          // Address bits (covers 0x00-0x2C).
) (
    input  logic                    clk,
    input  logic                    rst_n,

    // AXI4-Lite slave interface.
    input  logic [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  logic                    s_axi_awvalid,
    output logic                    s_axi_awready,
    input  logic [31:0]             s_axi_wdata,
    input  logic [3:0]              s_axi_wstrb,
    input  logic                    s_axi_wvalid,
    output logic                    s_axi_wready,
    output logic [1:0]              s_axi_bresp,
    output logic                    s_axi_bvalid,
    input  logic                    s_axi_bready,
    input  logic [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  logic                    s_axi_arvalid,
    output logic                    s_axi_arready,
    output logic [31:0]             s_axi_rdata,
    output logic [1:0]              s_axi_rresp,
    output logic                    s_axi_rvalid,
    input  logic                    s_axi_rready,

    // Datapath control signals (directly to/from PQC core).
    output logic                    ctrl_start,     // Pulse: begin operation.
    output logic                    ctrl_reset,     // Pulse: reset datapath.
    input  logic                    status_busy,
    input  logic                    status_done,
    input  logic                    status_error,
    output logic [31:0]             sec_level,
    output logic [31:0]             op_mode,        // 0=keygen, 1=encaps/sign, 2=decaps/verify.
    input  logic [31:0]             cycle_count,
    input  logic [31:0]             error_code,
    output logic [31:0]             data_in_addr,
    output logic [31:0]             data_in_len,
    output logic [31:0]             data_out_addr,
    input  logic [31:0]             data_out_len
);

    // =========================================================================
    // Internal registers
    // =========================================================================

    logic [31:0] ctrl_reg;
    logic [31:0] sec_level_reg;
    logic [31:0] op_mode_reg;
    logic [31:0] data_in_addr_reg;
    logic [31:0] data_in_len_reg;
    logic [31:0] data_out_addr_reg;

    // Constant registers.
    localparam logic [31:0] ALG_ID_VAL  = ALG_ID;
    localparam logic [31:0] VERSION_VAL = (VERSION_MAJ << 16) | (VERSION_MIN << 8) | VERSION_PAT;

    // Output assignments.
    assign sec_level     = sec_level_reg;
    assign op_mode       = op_mode_reg;
    assign data_in_addr  = data_in_addr_reg;
    assign data_in_len   = data_in_len_reg;
    assign data_out_addr = data_out_addr_reg;

    // CTRL register generates pulses.
    assign ctrl_start = ctrl_reg[0];
    assign ctrl_reset = ctrl_reg[1];

    // =========================================================================
    // AXI-Lite write channel
    // =========================================================================

    logic aw_handshake, w_handshake;
    logic [ADDR_WIDTH-1:0] aw_addr_latched;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
            aw_handshake  <= 1'b0;
            w_handshake   <= 1'b0;
            aw_addr_latched <= '0;
            ctrl_reg       <= '0;
            sec_level_reg  <= 32'd768;  // Default ML-KEM-768.
            op_mode_reg    <= '0;
            data_in_addr_reg  <= '0;
            data_in_len_reg   <= '0;
            data_out_addr_reg <= '0;
        end else begin
            // Auto-clear start pulse after one cycle.
            if (ctrl_reg[0]) ctrl_reg[0] <= 1'b0;
            if (ctrl_reg[1]) ctrl_reg[1] <= 1'b0;

            // Address channel handshake.
            if (s_axi_awvalid && !aw_handshake) begin
                s_axi_awready <= 1'b1;
                aw_addr_latched <= s_axi_awaddr;
                aw_handshake <= 1'b1;
            end else begin
                s_axi_awready <= 1'b0;
            end

            // Data channel handshake.
            if (s_axi_wvalid && !w_handshake) begin
                s_axi_wready <= 1'b1;
                w_handshake <= 1'b1;
            end else begin
                s_axi_wready <= 1'b0;
            end

            // When both address and data are received, write the register.
            if (aw_handshake && w_handshake) begin
                case (aw_addr_latched[5:2])  // Word-aligned offset.
                    4'h0: ctrl_reg           <= s_axi_wdata;  // 0x00 CTRL
                    4'h3: sec_level_reg      <= s_axi_wdata;  // 0x0C SEC_LEVEL
                    4'h4: op_mode_reg        <= s_axi_wdata;  // 0x10 OP_MODE
                    4'h8: data_in_addr_reg   <= s_axi_wdata;  // 0x20 DATA_IN_ADDR
                    4'h9: data_in_len_reg    <= s_axi_wdata;  // 0x24 DATA_IN_LEN
                    4'hA: data_out_addr_reg  <= s_axi_wdata;  // 0x28 DATA_OUT_ADDR
                    default: ;  // Read-only registers: ignore writes.
                endcase
                s_axi_bvalid <= 1'b1;
                aw_handshake <= 1'b0;
                w_handshake  <= 1'b0;
            end

            // Response handshake.
            if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    // =========================================================================
    // AXI-Lite read channel
    // =========================================================================

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_arready <= 1'b0;
            s_axi_rvalid  <= 1'b0;
            s_axi_rdata   <= '0;
            s_axi_rresp   <= 2'b00;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid) begin
                s_axi_arready <= 1'b1;
                s_axi_rvalid  <= 1'b1;

                case (s_axi_araddr[5:2])
                    4'h0: s_axi_rdata <= ctrl_reg;
                    4'h1: s_axi_rdata <= {29'b0, status_error, status_done, status_busy};
                    4'h2: s_axi_rdata <= ALG_ID_VAL;
                    4'h3: s_axi_rdata <= sec_level_reg;
                    4'h4: s_axi_rdata <= op_mode_reg;
                    4'h5: s_axi_rdata <= cycle_count;
                    4'h6: s_axi_rdata <= VERSION_VAL;
                    4'h7: s_axi_rdata <= error_code;
                    4'h8: s_axi_rdata <= data_in_addr_reg;
                    4'h9: s_axi_rdata <= data_in_len_reg;
                    4'hA: s_axi_rdata <= data_out_addr_reg;
                    4'hB: s_axi_rdata <= data_out_len;
                    default: s_axi_rdata <= 32'hDEAD_BEEF;
                endcase
            end else begin
                s_axi_arready <= 1'b0;
            end

            if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
