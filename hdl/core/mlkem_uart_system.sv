// mlkem_uart_system.sv - Everything between the board pins and the core
//
//   uart_rxd -> uart_rx -> uart_axi_bridge -> AXI-Lite -> axil_cdc -> pqc_mlkem_top
//   uart_txd <- uart_tx <-/        (clk)                    (clk_core)
//   trig_out = STATUS.busy, status_leds on the green / RGB LEDs
//
// Shared by every board top (hdl/xilinx/*/): a top only adds its clocking
// (MMCM for clk_core), its reset synchronisers, its pins and, on the Arty
// Z7 PS build, the Zynq PS that carries the UART. Vendor-agnostic.
//
// Clocks: clk drives the UART, the bridge and the LEDs (the board
// oscillator, 100 or 125 MHz); clk_core drives the ML-KEM core (206 MHz).
// The two only meet in axil_cdc and in two-flop synchronisers (bridge
// core_busy, status_leds), so the constraints treat them as asynchronous.
//
// trig_out comes straight from the core's STATUS.busy register (no logic):
// it rises on the core clock edge an operation starts and stays high for
// exactly CYCLE_COUNT core cycles (testbench/cdc/tb_bridge.sv checks it).

module mlkem_uart_system #(
    parameter int CLK_FREQ  = 100_000_000,   // clk frequency
    parameter int BAUD_RATE = 115200,
    parameter int N_RGB     = 4               // RGB LEDs on the board (4 Arty A7, 2 Arty Z7)
) (
    input  logic             clk,
    input  logic             rst_n,          // clk domain, synchronous release
    input  logic             clk_core,
    input  logic             core_rst_n,     // clk_core domain, synchronous release
    input  logic             in_reset,       // async: reset button held / clk_core not locked (LED only)

    input  logic             uart_rxd,
    output logic             uart_txd,
    output logic             trig_out,
    output logic [3:0]       led,
    output logic [N_RGB-1:0] rgb_r,
    output logic [N_RGB-1:0] rgb_g,
    output logic [N_RGB-1:0] rgb_b
);

    // =========================================================================
    // UART and protocol bridge (clk)
    // =========================================================================
    logic [7:0]  rx_byte, tx_byte;
    logic        rx_valid, tx_valid, tx_ready;
    logic        ev_ok, ev_err, ev_run;

    logic [15:0] awaddr, araddr;
    logic        awvalid, awready, wvalid, wready, bvalid, bready;
    logic        arvalid, arready, rvalid, rready;
    logic [31:0] wdata, rdata;
    logic [3:0]  wstrb;
    logic [1:0]  bresp, rresp;

    // Core status (clk_core domain).
    logic        status_busy, status_done, status_error;
    logic [1:0]  op_mode;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_rx (
        .clk (clk), .rst_n (rst_n), .rx (uart_rxd), .data (rx_byte), .valid (rx_valid)
    );

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_tx (
        .clk (clk), .rst_n (rst_n), .tx (uart_txd), .data (tx_byte), .valid (tx_valid), .ready (tx_ready)
    );

    uart_axi_bridge #(
        .TIMEOUT_CYCLES     (CLK_FREQ / 10),   // 100 ms inter-byte timeout
        .RUN_TIMEOUT_CYCLES (CLK_FREQ)         // 0x05 Run gives up after 1 s
    ) u_bridge (
        .clk (clk), .rst_n (rst_n), .core_busy (status_busy),
        .rx_byte (rx_byte), .rx_valid (rx_valid),
        .tx_byte (tx_byte), .tx_valid (tx_valid), .tx_ready (tx_ready),
        .m_axi_awaddr (awaddr), .m_axi_awvalid (awvalid), .m_axi_awready (awready),
        .m_axi_wdata (wdata), .m_axi_wstrb (wstrb), .m_axi_wvalid (wvalid), .m_axi_wready (wready),
        .m_axi_bresp (bresp), .m_axi_bvalid (bvalid), .m_axi_bready (bready),
        .m_axi_araddr (araddr), .m_axi_arvalid (arvalid), .m_axi_arready (arready),
        .m_axi_rdata (rdata), .m_axi_rresp (rresp), .m_axi_rvalid (rvalid), .m_axi_rready (rready),
        .ev_ok (ev_ok), .ev_err (ev_err), .ev_run (ev_run)
    );

    // =========================================================================
    // Clock crossing and the ML-KEM core (clk_core)
    // =========================================================================
    logic [15:0] c_awaddr, c_araddr;
    logic        c_awvalid, c_awready, c_wvalid, c_wready, c_bvalid, c_bready;
    logic        c_arvalid, c_arready, c_rvalid, c_rready;
    logic [31:0] c_wdata, c_rdata;
    logic [3:0]  c_wstrb;
    logic [1:0]  c_bresp, c_rresp;

    axil_cdc #(.ADDR_WIDTH (16)) u_cdc (
        .s_clk (clk), .s_rst_n (rst_n),
        .s_axi_awaddr (awaddr), .s_axi_awvalid (awvalid), .s_axi_awready (awready),
        .s_axi_wdata (wdata), .s_axi_wstrb (wstrb), .s_axi_wvalid (wvalid), .s_axi_wready (wready),
        .s_axi_bresp (bresp), .s_axi_bvalid (bvalid), .s_axi_bready (bready),
        .s_axi_araddr (araddr), .s_axi_arvalid (arvalid), .s_axi_arready (arready),
        .s_axi_rdata (rdata), .s_axi_rresp (rresp), .s_axi_rvalid (rvalid), .s_axi_rready (rready),
        .m_clk (clk_core), .m_rst_n (core_rst_n),
        .m_axi_awaddr (c_awaddr), .m_axi_awvalid (c_awvalid), .m_axi_awready (c_awready),
        .m_axi_wdata (c_wdata), .m_axi_wstrb (c_wstrb), .m_axi_wvalid (c_wvalid), .m_axi_wready (c_wready),
        .m_axi_bresp (c_bresp), .m_axi_bvalid (c_bvalid), .m_axi_bready (c_bready),
        .m_axi_araddr (c_araddr), .m_axi_arvalid (c_arvalid), .m_axi_arready (c_arready),
        .m_axi_rdata (c_rdata), .m_axi_rresp (c_rresp), .m_axi_rvalid (c_rvalid), .m_axi_rready (c_rready)
    );

    pqc_mlkem_top #(.AXI_ADDR_WIDTH (16)) u_mlkem (
        .clk (clk_core), .rst_n (core_rst_n),
        .s_axi_awaddr (c_awaddr), .s_axi_awvalid (c_awvalid), .s_axi_awready (c_awready),
        .s_axi_wdata (c_wdata), .s_axi_wstrb (c_wstrb), .s_axi_wvalid (c_wvalid), .s_axi_wready (c_wready),
        .s_axi_bresp (c_bresp), .s_axi_bvalid (c_bvalid), .s_axi_bready (c_bready),
        .s_axi_araddr (c_araddr), .s_axi_arvalid (c_arvalid), .s_axi_arready (c_arready),
        .s_axi_rdata (c_rdata), .s_axi_rresp (c_rresp), .s_axi_rvalid (c_rvalid), .s_axi_rready (c_rready),
        .irq (),
        .o_busy (status_busy), .o_done (status_done), .o_error (status_error), .o_op_mode (op_mode)
    );

    // Scope trigger: the core's STATUS.busy register, nothing in between.
    assign trig_out = status_busy;

    // =========================================================================
    // Status LEDs (clk) -- hold still while the core is busy
    // =========================================================================
    status_leds #(.CLK_FREQ (CLK_FREQ), .N_RGB (N_RGB)) u_leds (
        .clk (clk), .rst_n (rst_n), .in_reset (in_reset),
        .core_busy (status_busy), .core_done (status_done), .core_error (status_error), .core_op (op_mode),
        .ev_ok (ev_ok), .ev_err (ev_err), .ev_run (ev_run),
        .led (led), .rgb_r (rgb_r), .rgb_g (rgb_g), .rgb_b (rgb_b)
    );

endmodule
