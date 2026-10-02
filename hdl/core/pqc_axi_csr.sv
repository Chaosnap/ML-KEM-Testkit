// pqc_axi_csr.sv - AXI-Lite slave for PQC accelerator cores
//
// Implements the standard pqc-testkit register map and the host window
// into the data buffer. This module is shared by all PQC cores (ML-KEM,
// ML-DSA, SLH-DSA).
//
// Address map (16-bit AXI address):
//   0x0000 - 0x00FF  CSR registers (below)
//   0x4000 - 0x7FFF  Data buffer window (byte strobes honoured; a buffer
//                    smaller than 16 KB repeats, 8 KB: 0x6000 = 0x4000)
//   other            reads return 0xDEADBEEF, writes ignored
//
// Register map (matches pkg/fpga/device.go):
//   0x00 CTRL          WO  bit0 start pulse | bit1 reset pulse (reads 0)
//   0x04 STATUS        RO  busy | done | error
//   0x08 ALG_ID        RO  algorithm identifier
//   0x0C SEC_LEVEL     RW  security level
//   0x10 OP_MODE       RW  operation mode
//   0x14 CYCLE_COUNT   RO  cycle counter
//   0x18 VERSION       RO  core version
//   0x1C ERROR_CODE    RO  error code
//   0x20 DATA_IN_ADDR  RW  input buffer byte offset  (reset 0x0000; ML-KEM:
//                          multiple of 4, like DATA_OUT_ADDR)
//   0x24 DATA_IN_LEN   RW  input data length (informational)
//   0x28 DATA_OUT_ADDR RW  output buffer byte offset (reset DEFAULT_OUT_ADDR)
//   0x2C DATA_OUT_LEN  RO  output data length

module pqc_axi_csr #(
    parameter int ALG_ID       = 1,         // 1=ML-KEM, 2=ML-DSA, 3=SLH-DSA.
    parameter int VERSION_MAJ  = 1,
    parameter int VERSION_MIN  = 0,
    parameter int VERSION_PAT  = 0,
    parameter int ADDR_WIDTH   = 16,
    parameter int BUF_AW       = 12,        // Data buffer word address bits.
    parameter logic [31:0] DEFAULT_SEC_LEVEL = 32'd768,
    parameter logic [31:0] DEFAULT_IN_ADDR   = 32'h0000,
    parameter logic [31:0] DEFAULT_OUT_ADDR  = 32'h1800
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

    // Datapath control signals.
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
    input  logic [31:0]             data_out_len,

    // Data buffer host port (port A of pqc_data_buffer).
    output logic                    mem_en,
    output logic [3:0]              mem_we,
    output logic [BUF_AW-1:0]       mem_addr,
    output logic [31:0]             mem_din,
    input  logic [31:0]             mem_dout
);

    // Registered active-high synchronous reset, local to this module
    // (replicated by fanout; no inverter on the high-fanout net).
    (* max_fanout = 32 *) logic srst;
    always_ff @(posedge clk)
        srst <= !rst_n;

    localparam logic [31:0] ALG_ID_VAL  = ALG_ID;
    localparam logic [31:0] VERSION_VAL = (VERSION_MAJ << 16) | (VERSION_MIN << 8) | VERSION_PAT;

    function automatic logic is_mem(input logic [ADDR_WIDTH-1:0] a);
        return a[15:14] == 2'b01;
    endfunction

    function automatic logic is_csr(input logic [ADDR_WIDTH-1:0] a);
        return a[15:8] == 8'h00;
    endfunction

    // =========================================================================
    // Registers
    // =========================================================================

    logic [31:0] sec_level_reg;
    logic [31:0] op_mode_reg;
    logic [31:0] data_in_addr_reg;
    logic [31:0] data_in_len_reg;
    logic [31:0] data_out_addr_reg;

    assign sec_level     = sec_level_reg;
    assign op_mode       = op_mode_reg;
    assign data_in_addr  = data_in_addr_reg;
    assign data_in_len   = data_in_len_reg;
    assign data_out_addr = data_out_addr_reg;

    // =========================================================================
    // Write channel: accept AW and W independently, then commit.
    // =========================================================================

    logic                  aw_full, w_full;
    logic [ADDR_WIDTH-1:0] aw_addr;
    logic [31:0]           w_data;
    logic [3:0]            w_strb;
    logic                  do_write;

    assign s_axi_awready = !aw_full;
    assign s_axi_wready  = !w_full;
    assign s_axi_bresp   = 2'b00;
    assign do_write      = aw_full && w_full && !s_axi_bvalid;

    always_ff @(posedge clk) begin                 // Synchronous reset.
        if (srst) begin
            aw_full           <= 1'b0;
            w_full            <= 1'b0;
            aw_addr           <= '0;
            w_data            <= '0;
            w_strb            <= '0;
            s_axi_bvalid      <= 1'b0;
            ctrl_start        <= 1'b0;
            ctrl_reset        <= 1'b0;
            sec_level_reg     <= DEFAULT_SEC_LEVEL;
            op_mode_reg       <= '0;
            data_in_addr_reg  <= DEFAULT_IN_ADDR;
            data_in_len_reg   <= '0;
            data_out_addr_reg <= DEFAULT_OUT_ADDR;
        end else begin
            ctrl_start <= 1'b0;
            ctrl_reset <= 1'b0;

            if (s_axi_awvalid && s_axi_awready) begin
                aw_addr <= s_axi_awaddr;
                aw_full <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_data <= s_axi_wdata;
                w_strb <= s_axi_wstrb;
                w_full <= 1'b1;
            end

            if (do_write) begin
                if (is_csr(aw_addr)) begin
                    case (aw_addr[7:2])
                        6'h00: begin                        // 0x00 CTRL
                            ctrl_start <= w_data[0];
                            ctrl_reset <= w_data[1];
                        end
                        6'h03: sec_level_reg     <= w_data; // 0x0C SEC_LEVEL
                        6'h04: op_mode_reg       <= w_data; // 0x10 OP_MODE
                        6'h08: data_in_addr_reg  <= w_data; // 0x20 DATA_IN_ADDR
                        6'h09: data_in_len_reg   <= w_data; // 0x24 DATA_IN_LEN
                        6'h0A: data_out_addr_reg <= w_data; // 0x28 DATA_OUT_ADDR
                        default: ;                          // Read-only.
                    endcase
                end
                aw_full      <= 1'b0;
                w_full       <= 1'b0;
                s_axi_bvalid <= 1'b1;
            end

            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
        end
    end

    // =========================================================================
    // Read channel. Buffer reads take two extra cycles (BRAM + output register).
    // =========================================================================

    typedef enum logic [2:0] { R_IDLE, R_ISSUE, R_MEM, R_MEM2, R_RESP } rstate_t;
    rstate_t               rstate;
    logic [ADDR_WIDTH-1:0] ar_addr;

    assign s_axi_arready = (rstate == R_IDLE);
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rvalid  = (rstate == R_RESP);

    logic [31:0] csr_rdata;
    always_comb begin
        csr_rdata = 32'hDEAD_BEEF;
        if (is_csr(ar_addr)) begin
            case (ar_addr[7:2])
                6'h00: csr_rdata = 32'h0;
                6'h01: csr_rdata = {29'b0, status_error, status_done, status_busy};
                6'h02: csr_rdata = ALG_ID_VAL;
                6'h03: csr_rdata = sec_level_reg;
                6'h04: csr_rdata = op_mode_reg;
                6'h05: csr_rdata = cycle_count;
                6'h06: csr_rdata = VERSION_VAL;
                6'h07: csr_rdata = error_code;
                6'h08: csr_rdata = data_in_addr_reg;
                6'h09: csr_rdata = data_in_len_reg;
                6'h0A: csr_rdata = data_out_addr_reg;
                6'h0B: csr_rdata = data_out_len;
                default: ;
            endcase
        end
    end

    always_ff @(posedge clk) begin                 // Synchronous reset.
        if (srst) begin
            rstate      <= R_IDLE;
            ar_addr     <= '0;
            s_axi_rdata <= '0;
        end else begin
            case (rstate)
                R_IDLE: if (s_axi_arvalid) begin
                    ar_addr <= s_axi_araddr;
                    rstate  <= R_ISSUE;
                end
                R_ISSUE: begin
                    if (is_mem(ar_addr)) begin
                        // Port A is shared with writes: wait for a free cycle.
                        if (!do_write) rstate <= R_MEM;
                    end else begin
                        s_axi_rdata <= csr_rdata;
                        rstate      <= R_RESP;
                    end
                end
                R_MEM:  rstate <= R_MEM2;
                R_MEM2: begin
                    s_axi_rdata <= mem_dout;
                    rstate      <= R_RESP;
                end
                R_RESP: if (s_axi_rready) rstate <= R_IDLE;
                default: rstate <= R_IDLE;
            endcase
        end
    end

    // =========================================================================
    // Data buffer port A.
    // =========================================================================

    logic mem_wr, mem_rd;
    assign mem_wr   = do_write && is_mem(aw_addr);
    assign mem_rd   = (rstate == R_ISSUE) && is_mem(ar_addr) && !do_write;
    assign mem_en   = mem_wr || mem_rd;
    assign mem_we   = mem_wr ? w_strb : 4'b0000;
    assign mem_addr = mem_wr ? aw_addr[BUF_AW+1:2] : ar_addr[BUF_AW+1:2];
    assign mem_din  = w_data;

endmodule
