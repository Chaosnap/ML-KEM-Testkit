// sakura_x_top.sv - Board-level wrapper for SAKURA-X (SASEBO-GIII), main FPGA
//
// The ML-KEM-768 core runs in the board's cryptographic FPGA, the Kintex-7
// XC7K160T-1FBG676C (U11). Clocking, resets and pins only; everything else is
// hdl/core/mlkem_uart_system.sv (UART -> uart_axi_bridge -> axil_cdc ->
// pqc_mlkem_top, trigger, LEDs), exactly as on the Arty boards. Pin
// citations: constraints.xdc (SASEBO-GIII Specification v1.1, Tables 13-20,
// cross-checked against AMD's xc7k160tfbg676 package file).
//
// Host link: the board's own USB port (CN15, FT2232H channel A) through the
// Spartan-6 control FPGA running its FACTORY bitstream (sasebo_giii_ctrl,
// AIST). That bitstream turns USB bytes into accesses on the inter-FPGA local
// bus; sakura_lbus_bridge turns those back into the byte stream of the host
// protocol (docs/UART_PROTOCOL.md), which reaches mlkem_uart_system over a
// internal serial line at BAUD_RATE. The host opens the port as "sakura:<device>"
// (cpa_toolkit/sakura_lbus.py). hdl/core/ is unchanged.
//
// Optional second link: an external 2.5 V USB-UART adapter on the Kintex-7
// GPIO header CN8 (pins 1/2) still works -- both receive lines are ANDed
// (idle high; CN8.1 has a pull-up) and the reply goes to both. Use one link
// at a time.
//
//   !! CN8 is in a 2.5 V bank (HR, VCCO = 2.5 V). An adapter there must use
//   !! 2.5 V I/O; 3.3 V exceeds the pin's absolute maximum (VCCO + 0.55 V).
//   !! CN8 pin 14 is VIN, not logic.
//
// Clocks:
//   clk200   200 MHz differential oscillator U16 (K_CLK_OSC1, pins AA3/AA2).
//   clk_core 206.25 MHz = 200 MHz / 1 * 4.125 / 4 (VCO 825 MHz): the whole
//            ML-KEM core; equals cpa_toolkit/config.py's CORE_MHZ, as on the
//            Arty Z7, so the host's cycle <-> time conversion is unchanged.
//   clk      103.125 MHz = VCO / 8: UART, bridge, LEDs. axil_cdc crosses the
//            AXI-Lite bus between the two (they are treated as asynchronous).
//
// Pins (constraints.xdc):
//   - local bus to the Spartan-6 (lbus_*, pins from AIST's pin_sasebo_giii_k7.ucf)
//   - J8 osc_en_b = 1 (K_CLK_INH_B: keep both 200 MHz oscillators running,
//     AES reference uses the local-bus clock and disables these oscillators)
//   - CN8.1 (D19) uart_rxd  FPGA input  <- optional adapter TXD
//   - CN8.2 (N17) uart_txd  FPGA output -> optional adapter RXD
//   - CN8.3 (N16) trig_out  scope trigger = STATUS.busy (2.5 V logic: trigger
//                           level about 1.25 V); GND on CN8.11 or CN8.13
//   - LED1-LED10 (led[0..9]), see the mapping below
//   - SW4 (L23) reset push switch, pressed = high (schematic p.54, R212 pull-down)
//
// LEDs (status_leds.sv with N_RGB = 2; SAKURA-X has no RGB LEDs, so each
// colour channel of the two "RGB" indicators gets its own LED):
//   LED1-LED4  activity / done / error / heartbeat (led[3:0] of status_leds)
//   LED5       in reset: SW4 held or the core clock not locked (RGB 0 red)
//   LED6       running, dim (RGB 0 green)
//   LED7       host link: flash per reply frame (RGB 1 blue)
//   LED8       host link: rejected frame, 0.5 s (RGB 1 red)
//   LED9       0x05 completion notice flash (RGB 1 green)
//   LED10      MMCM locked (static; dark = reset or unlocked clock -- check SW4/SW3.7)
// Like on the Arty boards, nothing here switches while the core is busy.
//
// Build:
//   vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
// Program (JTAG on CN5):
//   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs <bit> [xvc-url]

module sakura_x_top #(
    parameter int CLK_FREQ       = 103_125_000,   // clk: MMCM CLKOUT1
    parameter int BAUD_RATE      = 115200,
    parameter bit BTN_ACTIVE_LOW = 1'b0,          // SW4: pressed = 1, released = 0
    parameter bit LED_ACTIVE_LOW = 1'b0           // LED1-LED10: 1 = lit
) (
    input  logic       clk200_p,      // 200 MHz oscillator U16, differential.
    input  logic       clk200_n,
    input  logic       pushsw,        // SW4 (K_PUSHSW): reset.

    // Local bus from the Spartan-6 (board USB, see above).
    input  logic [15:0] lbus_di_a,
    input  logic        lbus_wrn,
    input  logic        lbus_rdn,
    output logic [15:0] lbus_do,
    output logic        osc_en_b,     // J8, K_CLK_INH_B: 1 = oscillators on.

    // Optional external 2.5 V USB-UART adapter on CN8 (see above).
    input  logic       uart_rxd,      // CN8.1, FPGA input.
    output logic       uart_txd,      // CN8.2, FPGA output.

    // Oscilloscope trigger (CN8.3) = STATUS.busy.
    output logic       trig_out,

    // User LEDs LED1-LED10 of the Kintex-7.
    output logic [9:0] led
);

    // =========================================================================
    // Clock and reset
    // =========================================================================
    logic clk200;
    IBUFDS u_ibufds (.I(clk200_p), .IB(clk200_n), .O(clk200));

    // VCO = 200 MHz / 1 * 4.125 = 825 MHz (Kintex-7 -1: 600-1200 MHz);
    // CLKOUT0 = 825 / 4 = 206.25 MHz, CLKOUT1 = 825 / 8 = 103.125 MHz.
    logic clk, clk_mmcm, clk_core, clk_core_mmcm, mmcm_fb, mmcm_locked;
    logic btn;
    assign btn = BTN_ACTIVE_LOW ? !pushsw : pushsw;

    MMCME2_BASE #(
        .CLKIN1_PERIOD    (5.000),
        .DIVCLK_DIVIDE    (1),
        .CLKFBOUT_MULT_F  (4.125),
        .CLKOUT0_DIVIDE_F (4.0),
        .CLKOUT1_DIVIDE   (8)
    ) u_mmcm (
        .CLKIN1   (clk200),
        .CLKFBIN  (mmcm_fb),
        .CLKFBOUT (mmcm_fb),
        .CLKOUT0  (clk_core_mmcm),
        .CLKOUT1  (clk_mmcm),
        .LOCKED   (mmcm_locked),
        .RST      (btn),
        .PWRDWN   (1'b0),
        .CLKFBOUTB (), .CLKOUT0B (), .CLKOUT1B (), .CLKOUT2 (), .CLKOUT2B (),
        .CLKOUT3 (), .CLKOUT3B (), .CLKOUT4 (), .CLKOUT5 (), .CLKOUT6 ()
    );

    BUFG u_bufg_core (.I(clk_core_mmcm), .O(clk_core));
    BUFG u_bufg      (.I(clk_mmcm),      .O(clk));

    // Both domains come from the MMCM, so both are held in reset while it is
    // unlocked or SW4 is pressed: asserted asynchronously, released
    // synchronously to each clock.
    logic arst;
    assign arst = btn || !mmcm_locked;

    logic       core_rst_n;
    logic [3:0] core_rst_sync;
    always_ff @(posedge clk_core or posedge arst) begin
        if (arst)
            core_rst_sync <= '0;
        else
            core_rst_sync <= {core_rst_sync[2:0], 1'b1};
    end
    assign core_rst_n = core_rst_sync[3];

    logic       rst_n;
    logic [3:0] rst_sync;
    always_ff @(posedge clk or posedge arst) begin
        if (arst)
            rst_sync <= '0;
        else
            rst_sync <= {rst_sync[2:0], 1'b1};
    end
    assign rst_n = rst_sync[3];

    // =========================================================================
    // UART, bridge, clock crossing, ML-KEM core, trigger and LEDs
    // =========================================================================
    assign osc_en_b = 1'b1;

    // Board USB: local bus <-> byte stream. The two receive lines are ANDed
    // (both idle high); the system's reply goes to both links.
    logic lbus_ser_tx, sys_rxd, sys_txd;

    // Share the baud rate with u_sys and the optional CN8 UART.
    sakura_lbus_bridge #(.CLK_FREQ (CLK_FREQ), .BAUD_RATE (BAUD_RATE)) u_lbus (
        .clk       (clk),
        .rst_n     (rst_n),
        .lbus_di_a (lbus_di_a),
        .lbus_wrn  (lbus_wrn),
        .lbus_rdn  (lbus_rdn),
        .lbus_do   (lbus_do),
        .ser_tx    (lbus_ser_tx),
        .ser_rx    (sys_txd)
    );

    assign sys_rxd  = lbus_ser_tx & uart_rxd;
    assign uart_txd = sys_txd;

    logic [3:0] led_sys;
    logic [1:0] rgb_r, rgb_g;
    /* verilator lint_off UNUSEDSIGNAL */   // rgb_b[0]: see below
    logic [1:0] rgb_b;
    /* verilator lint_on UNUSEDSIGNAL */

    mlkem_uart_system #(
        .CLK_FREQ  (CLK_FREQ),
        .BAUD_RATE (BAUD_RATE),
        .N_RGB     (2)
    ) u_sys (
        .clk        (clk),
        .rst_n      (rst_n),
        .clk_core   (clk_core),
        .core_rst_n (core_rst_n),
        .in_reset   (arst),
        .uart_rxd   (sys_rxd),
        .uart_txd   (sys_txd),
        .trig_out   (trig_out),
        .led        (led_sys),
        .rgb_r      (rgb_r),
        .rgb_g      (rgb_g),
        .rgb_b      (rgb_b)
    );

    // rgb_b[0] (the power indicator's blue channel) is never lit.
    logic [9:0] led_on;
    assign led_on = {mmcm_locked, rgb_g[1], rgb_r[1], rgb_b[1], rgb_g[0], rgb_r[0], led_sys};
    assign led    = LED_ACTIVE_LOW ? ~led_on : led_on;

endmodule
