// arty_z7_top.sv - Board-level wrapper for Arty Z7-10 / Arty Z7-20 (Zynq-7000)
//
// Clocking, resets and pins; everything else is hdl/core/mlkem_uart_system.sv
// (UART -> uart_axi_bridge -> axil_cdc -> pqc_mlkem_top, trigger, LEDs),
// exactly as on the Arty A7 -- this file only differs in the PL pin set and
// the clock math for this board. See constraints.xdc for pin citations.
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
//            crosses the AXI-Lite bus between them. 206.25 MHz is
//            cpa_toolkit/config.py's CORE_MHZ.
//
// Pin assignments (constraints.xdc; all checked against Digilent's official
// Arty-Z7-20-Master.xdc):
//   - sysclk: 125 MHz PL oscillator (H16).
//   - UART TX/RX: Pmod JA pin 1 (Y18, FPGA TX) / pin 2 (Y19, FPGA RX) --
//     EXTERNAL 3.3 V USB-UART adapter required (see the big comment above);
//     GND on JA pin 5 or 11.
//   - trig_out: Pmod JA pin 3 (Y16). Oscilloscope trigger/capture-enable, =
//     status_busy, same role as hdl/xilinx/arty_a7/arty_a7_top.sv's trig_out.
//   - LD0-LD3 (led[0..3]): activity / done / error / heartbeat (R14/P14/N16/M14)
//   - LD4, LD5 RGB: power-reset / host link (+ completion notice) -- see
//     hdl/core/status_leds.sv
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

    // Oscilloscope trigger (Pmod JA pin 3) = STATUS.busy.
    output logic       trig_out,

    // Green LEDs LD0-LD3 and RGB LEDs LD4, LD5.
    output logic [3:0] led,
    output logic [1:0] rgb_r,
    output logic [1:0] rgb_g,
    output logic [1:0] rgb_b
);

    // =========================================================================
    // Clock and reset
    // =========================================================================

    // Core clock: VCO = 125 MHz / 1 * 8.25 = 1031.25 MHz, / 5 = 206.25 MHz
    // -- exact (DIVCLK_DIVIDE=1 keeps the phase-detector input at a clean
    // 125 MHz; CLKFBOUT_MULT_F=8.25 and CLKOUT0_DIVIDE_F=5.0 are both valid
    // MMCME2 0.125-step values).
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
    // The registers power up at 0, so the design also starts in reset.
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
    // UART, bridge, clock crossing, ML-KEM core, trigger and LEDs
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
