// arty_z7_top.sv - Board-level wrapper for Arty Z7-10 / Arty Z7-20 (Zynq-7000)
//
// UART RX -> uart_axi_bridge -> AXI-Lite -> axil_cdc -> pqc_mlkem_top
//   (CSR registers + 8 KB data buffer + ML-KEM datapath) -> UART TX
//
// Same hdl/core/*.sv RTL and the same board-wrapper architecture as
// hdl/xilinx/arty_a7/arty_a7_top.sv -- this file only differs in the PL
// (programmable-logic) pin set and clock math for this board. See
// constraints.xdc in this directory for pin citations/caveats.
//
// IMPORTANT - this is a PURE-PL design, no Zynq Processing System (PS7):
// the Arty Z7's on-board USB-UART chip is wired only to the Zynq PS's MIO
// pins (per Digilent's own Arty Z7 reference material), not to any PL pin,
// so a pure-PL design -- this one, deliberately kept that way so
// hdl/core/*.sv and the host-side protocol (docs/UART_PROTOCOL.md) stay completely
// unchanged from the Arty A7 build -- cannot reach it. uart_rxd/uart_txd
// here are instead routed to a Pmod header pin pair: plug a SEPARATE
// external USB-UART cable into that Pmod (same idea as a generic FTDI TTL
// cable), rather than the on-board USB-micro port. A Zynq part with no PS7
// instantiated behaves exactly like a plain 7-series FPGA for synthesis,
// implementation and bitstream loading (JTAG) -- nothing else about the
// Vivado flow changes.
//
// Clocks:
//   clk      125 MHz PL system-clock oscillator (sysclk): UART, bridge, LEDs.
//            Arty Z7's PL clock is 125 MHz, not the Arty A7's 100 MHz.
//   clk_core 206.25 MHz from an MMCM (125 MHz / 1 * 8.25 / 5 -- DIVCLK_DIVIDE=1,
//            CLKFBOUT_MULT_F=8.25, CLKOUT0_DIVIDE_F=5.0, VCO=1031.25 MHz,
//            all exact, no rounding): the whole ML-KEM core; axil_cdc
//            crosses the AXI-Lite bus between them. 206.25 MHz happens to
//            match CPAProtectEvaluation/config.py's existing CORE_MHZ
//            constant exactly, so that toolkit needs no edit for this board.
//
// Pin assignments (constraints.xdc; all checked against Digilent's official
// Arty-Z7-20-Master.xdc):
//   - sysclk: 125 MHz PL oscillator (H16).
//   - UART TX/RX: Pmod JA pin 1 (Y18, FPGA TX) / pin 2 (Y19, FPGA RX) --
//     EXTERNAL 3.3 V USB-UART adapter required (see the big comment above);
//     GND on JA pin 5 or 11.
//   - trig_out: Pmod JA pin 3 (Y16). Oscilloscope trigger/capture-enable, =
//     status_busy, same role as hdl/xilinx/arty_a7/arty_a7_top.sv's trig_out.
//   - LED[0..3]: busy / done / error / heartbeat (R14/P14/N16/M14), same
//     meaning as the Arty A7 build.
//   - BTN[0]: reset, active-high push button (D19).
//
// Build:
//   vivado -mode batch -source scripts/vivado/build_arty_z7.tcl -tclargs mlkem
//
// Test:
//   uv run python scripts/test/acvp_test.py uart -p <serial port>
//   (same host tooling as Arty A7 -- point -d at whatever serial device the
//   external USB-UART cable on Pmod JA enumerates as, not the board's
//   on-board USB-micro port, which carries only JTAG + the PS's own UART.)

module arty_z7_top #(
    parameter int CLK_FREQ  = 125_000_000,
    parameter int BAUD_RATE = 115200
) (
    input  logic       sysclk,        // 125 MHz PL system-clock oscillator.
    input  logic       btn0,          // BTN[0], active-high: pressed = reset.

    // UART, via an EXTERNAL cable on a Pmod header -- see the module-level
    // comment above for why (the on-board USB-UART chip is PS-MIO only).
    input  logic       uart_rxd,      // UART receive (FPGA input).
    output logic       uart_txd,      // UART transmit (FPGA output).

    // Oscilloscope capture-enable / trigger (Pmod JA pin 3). Same role as
    // arty_a7_top.sv's trig_out: high for the whole core operation
    // (= status_busy), so the scope arms on the rising edge and captures
    // the computation window.
    output logic       trig_out,

    // LEDs for status indication.
    output logic [3:0] led
);

    // =========================================================================
    // Clock and reset
    // =========================================================================

    logic clk;
    logic rst_n;
    logic [3:0] rst_sync;

    assign clk = sysclk;

    // Core clock: VCO = 125 MHz / 1 * 8.25 = 1031.25 MHz, / 5 = 206.25 MHz
    // -- exact (DIVCLK_DIVIDE=1 keeps the phase-detector input at a clean
    // 125 MHz; CLKFBOUT_MULT_F=8.25 and CLKOUT0_DIVIDE_F=5.0 are both valid
    // MMCME2 0.125-step values). See the file header for why 206.25 MHz was
    // chosen over trying to hit the Arty A7 build's "206.00 MHz" nominal.
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

    // BTN0 asserts reset asynchronously; release is synchronised to clk
    // through a 4-stage shift register. The registers power up at 0, so the
    // design also starts in reset after configuration.
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

    // AXI-Lite in the core clock domain.
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
        .irq            (),  // Not used.
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

    // Scope trigger: same signal as LED0, broken out to its own header pin
    // so a probe doesn't have to touch the LED.
    assign trig_out = status_busy;

endmodule
