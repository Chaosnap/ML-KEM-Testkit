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
// LD0-3 R14/P14/N16/M14, RGB LD4/LD5, trig_out Pmod JA pin 3 (Y16). DDR and
// MIO are dedicated PS pins, placed by the PS7 IP. The UART, bridge, core,
// trigger and LEDs are hdl/core/mlkem_uart_system.sv, as on the other boards.
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

    // Green LEDs LD0-LD3 and RGB LEDs LD4, LD5 (hdl/core/status_leds.sv).
    output logic [3:0]  led,
    output logic [1:0]  rgb_r,
    output logic [1:0]  rgb_g,
    output logic [1:0]  rgb_b
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

    // BTN0 asserts reset asynchronously; release is synchronised to sysclk.
    logic       rst_n;
    logic [3:0] rst_sync;
    always_ff @(posedge sysclk or posedge btn0) begin
        if (btn0)
            rst_sync <= '0;
        else
            rst_sync <= {rst_sync[2:0], 1'b1};
    end
    assign rst_n = rst_sync[3];

    // =========================================================================
    // UART (to/from PS UART1), bridge, clock crossing, core, trigger, LEDs
    // =========================================================================

    mlkem_uart_system #(
        .CLK_FREQ  (CLK_FREQ),
        .BAUD_RATE (BAUD_RATE),
        .N_RGB     (2)
    ) u_sys (
        .clk        (sysclk),
        .rst_n      (rst_n),
        .clk_core   (clk_core),
        .core_rst_n (core_rst_n),
        .in_reset   (core_arst),
        .uart_rxd   (uart_rxd),
        .uart_txd   (uart_txd),
        .trig_out   (trig_out),
        .led        (led),
        .rgb_r      (rgb_r),
        .rgb_g      (rgb_g),
        .rgb_b      (rgb_b)
    );

endmodule
