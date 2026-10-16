// arty_a7_top.sv - Board-level wrapper for Arty A7-35T / A7-100T
//
// Clocking, resets and pins; everything else is hdl/core/mlkem_uart_system.sv
// (UART -> uart_axi_bridge -> axil_cdc -> pqc_mlkem_top, trigger, LEDs).
//
// Clocks:
//   clk      100 MHz board oscillator: UART, bridge, LEDs
//   clk_core 206.00 MHz from an MMCM (100 MHz / 5 * 51.5 / 5): the whole
//            ML-KEM core; axil_cdc crosses the AXI-Lite bus between them.
//
// Pins (constraints.xdc, checked against Digilent's Arty-A7-100-Master.xdc):
//   - UART TX/RX via the on-board USB-UART bridge (115200 8N1)
//   - trig_out: Pmod JA pin 1 (G13). Scope trigger = STATUS.busy: rises on
//     the core clock edge an operation starts, high for CYCLE_COUNT cycles.
//   - LD4-LD7 (led[0..3]): activity / done / error / heartbeat
//   - LD0-LD3 RGB (above BTN0-BTN3): power-reset / host link / completion
//     notice / last operation -- see hdl/core/status_leds.sv
//   - BTN0: reset (active-high push button)
//
// Build:
//   vivado -mode batch -source scripts/vivado/build_arty_a7.tcl -tclargs arty-a7-100t mlkem
//
// Test (host protocol: docs/UART_PROTOCOL.md):
//   uv run python scripts/test/acvp_test.py uart -p <serial port>

module arty_a7_top #(
    parameter int CLK_FREQ  = 100_000_000,
    parameter int BAUD_RATE = 115200
) (
    input  logic       clk_100mhz,    // 100 MHz oscillator.
    input  logic       btn0,          // BTN[0], active-high: pressed = reset.

    // UART (on-board USB-UART bridge).
    input  logic       uart_rxd,      // UART receive (FPGA input).
    output logic       uart_txd,      // UART transmit (FPGA output).

    // Oscilloscope trigger (Pmod JA pin 1) = STATUS.busy.
    output logic       trig_out,

    // Green LEDs LD4-LD7 and RGB LEDs LD0-LD3.
    output logic [3:0] led,
    output logic [3:0] rgb_r,
    output logic [3:0] rgb_g,
    output logic [3:0] rgb_b
);

    // =========================================================================
    // Clock and reset
    // =========================================================================

    // Core clock: VCO = 100 MHz / 5 * 51.5 = 1030 MHz, / 5 = 206.00 MHz.
    logic clk_core, clk_core_mmcm, mmcm_fb, mmcm_locked;

    MMCME2_BASE #(
        .CLKIN1_PERIOD    (10.0),
        .DIVCLK_DIVIDE    (5),
        .CLKFBOUT_MULT_F  (51.5),
        .CLKOUT0_DIVIDE_F (5.0)
    ) u_mmcm (
        .CLKIN1   (clk_100mhz),
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

    // BTN0 asserts reset asynchronously; release is synchronised to the
    // 100 MHz clock. The registers power up at 0, so the design also starts
    // in reset after configuration.
    logic       rst_n;
    logic [3:0] rst_sync;
    always_ff @(posedge clk_100mhz or posedge btn0) begin
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
        .N_RGB     (4)
    ) u_sys (
        .clk        (clk_100mhz),
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
