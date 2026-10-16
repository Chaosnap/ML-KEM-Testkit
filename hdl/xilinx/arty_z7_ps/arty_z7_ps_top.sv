// arty_z7_ps_top.sv - Arty Z7-20 wrapper, host link over the ON-BOARD USB port
//
// Same core and PL datapath as hdl/xilinx/arty_z7/arty_z7_top.sv (the
// pure-PL variant, which needs an external USB-UART on Pmod JA). The only
// difference is where the UART comes from:
//
//   host USB --FTDI ch.B--> MIO 14/15 --> PS UART0
//                                           |  ARM: sw/arty_z7_uart_fwd
//                                           |  (copies bytes both ways)
//                                         PS UART1 --EMIO--> uart_rx / uart_tx (PL)
//
// On the Arty Z7 the on-board USB serial is wired only to PS MIO pins, so a
// Zynq Processing System (zynq_ps block design, built by
// scripts/vivado/build_arty_z7_ps.tcl from Digilent's board preset) plus a
// small bare-metal forwarding program is the only way to reach the PL from
// it. Host tools and protocol are unchanged: connect to the board's ...1
// serial interface at 115200.
//
// The PS7 has no AXI master/slave or FCLK ports enabled: the core still
// runs from the PL's own 125 MHz sysclk, exactly as in the pure-PL variant.
// DDR keeps Digilent's preset (the program runs from on-chip memory and
// never touches DDR, but ps7_init then matches every stock Arty Z7 design).
//
// Clocks:
//   clk      125 MHz PL sysclk: UART, bridge, LEDs.
//   clk_core 206.25 MHz (MMCM, 125 / 1 * 8.25 / 5): the ML-KEM core.
//
// Pins (constraints.xdc in this directory): sysclk H16, BTN0 D19,
// LD0-3 R14/P14/N16/M14, trig_out Pmod JA pin 3 (Y16). DDR and MIO are
// dedicated PS pins, placed by the PS7 IP.
//
// Build: vivado -mode batch -source scripts/vivado/build_arty_z7_ps.tcl
// Load:  uv run scripts/board/boot_arty_z7_ps.py   (on the host, after every power-up)

module arty_z7_ps_top #(
    parameter int CLK_FREQ  = 125_000_000,
    parameter int BAUD_RATE = 115200
) (
    input  logic        sysclk,       // 125 MHz PL system-clock oscillator.
    input  logic        btn0,         // BTN[0], active-high: pressed = reset.

    // Zynq PS dedicated pins (DDR + fixed IO).
    inout  wire  [14:0] DDR_addr,
    inout  wire  [2:0]  DDR_ba,
    inout  wire         DDR_cas_n,
    inout  wire         DDR_ck_n,
    inout  wire         DDR_ck_p,
    inout  wire         DDR_cke,
    inout  wire         DDR_cs_n,
    inout  wire  [3:0]  DDR_dm,
    inout  wire  [31:0] DDR_dq,
    inout  wire  [3:0]  DDR_dqs_n,
    inout  wire  [3:0]  DDR_dqs_p,
    inout  wire         DDR_odt,
    inout  wire         DDR_ras_n,
    inout  wire         DDR_reset_n,
    inout  wire         DDR_we_n,
    inout  wire         FIXED_IO_ddr_vrn,
    inout  wire         FIXED_IO_ddr_vrp,
    inout  wire  [53:0] FIXED_IO_mio,
    inout  wire         FIXED_IO_ps_clk,
    inout  wire         FIXED_IO_ps_porb,
    inout  wire         FIXED_IO_ps_srstb,

    // Oscilloscope capture-enable / trigger (Pmod JA pin 3), = status_busy.
    output logic        trig_out,

    // LEDs for status indication.
    output logic [3:0]  led
);

    // =========================================================================
    // Zynq PS: UART1 over EMIO is the host link
    // =========================================================================

    logic uart_rxd;   // PS UART1 TX -> PL (asynchronous; uart_rx synchronises it).
    logic uart_txd;   // PL -> PS UART1 RX.

    zynq_ps_wrapper u_ps (
        .DDR_addr          (DDR_addr),
        .DDR_ba            (DDR_ba),
        .DDR_cas_n         (DDR_cas_n),
        .DDR_ck_n          (DDR_ck_n),
        .DDR_ck_p          (DDR_ck_p),
        .DDR_cke           (DDR_cke),
        .DDR_cs_n          (DDR_cs_n),
        .DDR_dm            (DDR_dm),
        .DDR_dq            (DDR_dq),
        .DDR_dqs_n         (DDR_dqs_n),
        .DDR_dqs_p         (DDR_dqs_p),
        .DDR_odt           (DDR_odt),
        .DDR_ras_n         (DDR_ras_n),
        .DDR_reset_n       (DDR_reset_n),
        .DDR_we_n          (DDR_we_n),
        .FIXED_IO_ddr_vrn  (FIXED_IO_ddr_vrn),
        .FIXED_IO_ddr_vrp  (FIXED_IO_ddr_vrp),
        .FIXED_IO_mio      (FIXED_IO_mio),
        .FIXED_IO_ps_clk   (FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb  (FIXED_IO_ps_porb),
        .FIXED_IO_ps_srstb (FIXED_IO_ps_srstb),
        .UART_1_0_rxd      (uart_txd),
        .UART_1_0_txd      (uart_rxd)
    );

    // =========================================================================
    // Clock and reset
    // =========================================================================

    logic clk;
    logic rst_n;
    logic [3:0] rst_sync;

    assign clk = sysclk;

    // Core clock: VCO = 125 MHz / 1 * 8.25 = 1031.25 MHz, / 5 = 206.25 MHz.
    logic clk_core, clk_core_mmcm, mmcm_fb, mmcm_locked;

    MMCME2_BASE #(
        .CLKIN1_PERIOD    (8.000),
        .DIVCLK_DIVIDE    (1),
        .CLKFBOUT_MULT_F  (8.25),
        .CLKOUT0_DIVIDE_F (5.0)
    ) u_mmcm (
        .CLKIN1   (sysclk),
        .CLKFBIN  (mmcm_fb),
        .CLKFBOUT (mmcm_fb),
        .CLKOUT0  (clk_core_mmcm),
        .LOCKED   (mmcm_locked),
        .RST      (btn0),
        .PWRDWN   (1'b0),
        .CLKFBOUTB (), .CLKOUT0B (), .CLKOUT1 (), .CLKOUT1B (), .CLKOUT2 (),
        .CLKOUT2B (), .CLKOUT3 (), .CLKOUT3B (), .CLKOUT4 (), .CLKOUT5 (), .CLKOUT6 ()
    );

    BUFG u_bufg_core (.I(clk_core_mmcm), .O(clk_core));

    // Core reset: asserted asynchronously by BTN0 or while the MMCM is
    // unlocked, released synchronously to clk_core.
    logic       core_rst_n, core_arst;
    logic [3:0] core_rst_sync;
    assign core_arst = btn0 || !mmcm_locked;
    always_ff @(posedge clk_core or posedge core_arst) begin
        if (core_arst)
            core_rst_sync <= '0;
        else
            core_rst_sync <= {core_rst_sync[2:0], 1'b1};
    end
    assign core_rst_n = core_rst_sync[3];

    // BTN0 asserts reset asynchronously; release is synchronised to clk.
    always_ff @(posedge clk or posedge btn0) begin
        if (btn0)
            rst_sync <= '0;
        else
            rst_sync <= {rst_sync[2:0], 1'b1};
    end
    assign rst_n = rst_sync[3];

    // =========================================================================
    // UART and protocol bridge
    // =========================================================================

    logic [15:0] axi_awaddr;
    logic        axi_awvalid, axi_awready;
    logic [31:0] axi_wdata;
    logic [3:0]  axi_wstrb;
    logic        axi_wvalid, axi_wready;
    logic [1:0]  axi_bresp;
    logic        axi_bvalid, axi_bready;
    logic [15:0] axi_araddr;
    logic        axi_arvalid, axi_arready;
    logic [31:0] axi_rdata;
    logic [1:0]  axi_rresp;
    logic        axi_rvalid, axi_rready;

    // Core status (clk_core domain): LEDs, trig_out and the bridge's 0x05 Run.
    logic status_busy, status_done, status_error;

    logic [7:0]  rx_byte;
    logic        rx_valid;
    logic [7:0]  tx_byte;
    logic        tx_valid;
    logic        tx_ready;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_rx (
        .clk      (clk),
        .rst_n    (rst_n),
        .rx       (uart_rxd),
        .data     (rx_byte),
        .valid    (rx_valid)
    );

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_tx (
        .clk      (clk),
        .rst_n    (rst_n),
        .tx       (uart_txd),
        .data     (tx_byte),
        .valid    (tx_valid),
        .ready    (tx_ready)
    );

    uart_axi_bridge #(
        .TIMEOUT_CYCLES (CLK_FREQ / 10)   // 100 ms inter-byte timeout.
    ) u_bridge (
        .clk           (clk),
        .rst_n         (rst_n),
        .core_busy     (status_busy),     // 0x05 Run: synchronised inside.
        .rx_byte       (rx_byte),
        .rx_valid      (rx_valid),
        .tx_byte       (tx_byte),
        .tx_valid      (tx_valid),
        .tx_ready      (tx_ready),
        .m_axi_awaddr  (axi_awaddr),
        .m_axi_awvalid (axi_awvalid),
        .m_axi_awready (axi_awready),
        .m_axi_wdata   (axi_wdata),
        .m_axi_wstrb   (axi_wstrb),
        .m_axi_wvalid  (axi_wvalid),
        .m_axi_wready  (axi_wready),
        .m_axi_bresp   (axi_bresp),
        .m_axi_bvalid  (axi_bvalid),
        .m_axi_bready  (axi_bready),
        .m_axi_araddr  (axi_araddr),
        .m_axi_arvalid (axi_arvalid),
        .m_axi_arready (axi_arready),
        .m_axi_rdata   (axi_rdata),
        .m_axi_rresp   (axi_rresp),
        .m_axi_rvalid  (axi_rvalid),
        .m_axi_rready  (axi_rready)
    );

    // =========================================================================
    // PQC ML-KEM accelerator core
    // =========================================================================

    logic [15:0] c_awaddr, c_araddr;
    logic        c_awvalid, c_awready, c_wvalid, c_wready, c_bvalid, c_bready;
    logic        c_arvalid, c_arready, c_rvalid, c_rready;
    logic [31:0] c_wdata, c_rdata;
    logic [3:0]  c_wstrb;
    logic [1:0]  c_bresp, c_rresp;

    axil_cdc #(
        .ADDR_WIDTH (16)
    ) u_cdc (
        .s_clk         (clk),
        .s_rst_n       (rst_n),
        .s_axi_awaddr  (axi_awaddr),
        .s_axi_awvalid (axi_awvalid),
        .s_axi_awready (axi_awready),
        .s_axi_wdata   (axi_wdata),
        .s_axi_wstrb   (axi_wstrb),
        .s_axi_wvalid  (axi_wvalid),
        .s_axi_wready  (axi_wready),
        .s_axi_bresp   (axi_bresp),
        .s_axi_bvalid  (axi_bvalid),
        .s_axi_bready  (axi_bready),
        .s_axi_araddr  (axi_araddr),
        .s_axi_arvalid (axi_arvalid),
        .s_axi_arready (axi_arready),
        .s_axi_rdata   (axi_rdata),
        .s_axi_rresp   (axi_rresp),
        .s_axi_rvalid  (axi_rvalid),
        .s_axi_rready  (axi_rready),
        .m_clk         (clk_core),
        .m_rst_n       (core_rst_n),
        .m_axi_awaddr  (c_awaddr),
        .m_axi_awvalid (c_awvalid),
        .m_axi_awready (c_awready),
        .m_axi_wdata   (c_wdata),
        .m_axi_wstrb   (c_wstrb),
        .m_axi_wvalid  (c_wvalid),
        .m_axi_wready  (c_wready),
        .m_axi_bresp   (c_bresp),
        .m_axi_bvalid  (c_bvalid),
        .m_axi_bready  (c_bready),
        .m_axi_araddr  (c_araddr),
        .m_axi_arvalid (c_arvalid),
        .m_axi_arready (c_arready),
        .m_axi_rdata   (c_rdata),
        .m_axi_rresp   (c_rresp),
        .m_axi_rvalid  (c_rvalid),
        .m_axi_rready  (c_rready)
    );

    pqc_mlkem_top #(
        .AXI_ADDR_WIDTH (16)
    ) u_mlkem (
        .clk            (clk_core),
        .rst_n          (core_rst_n),
        .s_axi_awaddr   (c_awaddr),
        .s_axi_awvalid  (c_awvalid),
        .s_axi_awready  (c_awready),
        .s_axi_wdata    (c_wdata),
        .s_axi_wstrb    (c_wstrb),
        .s_axi_wvalid   (c_wvalid),
        .s_axi_wready   (c_wready),
        .s_axi_bresp    (c_bresp),
        .s_axi_bvalid   (c_bvalid),
        .s_axi_bready   (c_bready),
        .s_axi_araddr   (c_araddr),
        .s_axi_arvalid  (c_arvalid),
        .s_axi_arready  (c_arready),
        .s_axi_rdata    (c_rdata),
        .s_axi_rresp    (c_rresp),
        .s_axi_rvalid   (c_rvalid),
        .s_axi_rready   (c_rready),
        .irq            (),
        .o_busy         (status_busy),
        .o_done         (status_done),
        .o_error        (status_error)
    );

    // =========================================================================
    // LED indicators
    // =========================================================================

    logic [25:0] heartbeat_cnt;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            heartbeat_cnt <= '0;
        else
            heartbeat_cnt <= heartbeat_cnt + 26'd1;
    end

    assign led[0] = status_busy;            // LED0: operation in progress.
    assign led[1] = status_done;            // LED1: operation complete.
    assign led[2] = status_error;           // LED2: error.
    assign led[3] = heartbeat_cnt[25];      // LED3: heartbeat (~1.9 Hz at 125 MHz).

    assign trig_out = status_busy;          // Scope trigger, Pmod JA pin 3.

endmodule
