## SAKURA-X (SASEBO-GIII) XDC constraints for pqc_mlkem (top = sakura_x_top)
## Main FPGA: Kintex-7 XC7K160T-1FBG676C (U11).
##
## Pin sources: "SASEBO-GIII Specification" v1.1 (AIST, 26 Apr 2013), Section 3.1
## (Kintex-7 I/O assignments), Tables 13, 17, 19, 20, and Table 7 (CN8 pinout).
## Every pin's bank / HR-HP type was checked against AMD's package file
## xc7k160tfbg676pkg.txt. Banks 13-16 (CN8, LEDs, switches) are HR at 2.5 V;
## bank 34 (oscillators) is HP at 1.5 V.

## -----------------------------------------------------------------------------
## Clock: 200 MHz differential oscillator U16 (Table 13: K_CLK_OSC1_HSTL_P/N =
## AA3/AA2, IO_L12P/N_T1_MRCC_34). U15 (K_CLK_OSC0, AB2/AC2, SRCC) is the other
## 200 MHz oscillator; either works. LVDS input, no internal termination (the
## same setting a public SASEBO-GIII design uses on these pins). Both
## oscillators are enabled by SW3.7 = OFF (factory default, Table 12).
## -----------------------------------------------------------------------------
## Bank 34 also holds lbus_do (LVCMOS15), so its VCCO is 1.5 V: an LVDS INPUT is legal in an
## HP bank at VCCO != 1.8 V only with the internal termination off (UG471) -- keep DIFF_TERM FALSE.
set_property -dict {PACKAGE_PIN AA3 IOSTANDARD LVDS DIFF_TERM FALSE} [get_ports clk200_p]
set_property -dict {PACKAGE_PIN AA2 IOSTANDARD LVDS DIFF_TERM FALSE} [get_ports clk200_n]
create_clock -period 5.000 -name clk200 [get_ports clk200_p]

## MMCM outputs: CLKOUT0 = 206.25 MHz core clock, CLKOUT1 = 103.125 MHz bridge
## clock. They are related (2:1) but only meet through axil_cdc, whose
## crossing values are held stable around toggle handshakes -- timed as
## asynchronous, exactly like the Arty builds.
set_clock_groups -asynchronous \
    -group [get_clocks -of_objects [get_pins u_mmcm/CLKOUT1]] \
    -group [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]

## -----------------------------------------------------------------------------
## Reset: user push switch SW4 (Table 17: K_PUSHSW = L23), pressed = low
## (sakura_x_top BTN_ACTIVE_LOW = 1). If LED5 stays lit after programming,
## the switch is active-high on your board: set BTN_ACTIVE_LOW to 0.
## -----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN L23 IOSTANDARD LVCMOS25} [get_ports pushsw]

## -----------------------------------------------------------------------------
## Local bus to the Spartan-6 control FPGA = host link over the board USB
## (CN15). Pins copied from AIST's reference design for the factory Spartan-6
## bitstream (AIST package, not in the repo: sasebo_giii_aes/pin_sasebo_giii_k7.ucf and the
## matching sasebo_giii_ctrl/pin_sasebo_giii_sp6.ucf); they are the KS_HPIC /
## KS_HRIC interconnects of spec Table 29. lbus_do is the only direction the
## Kintex-7 drives. lbus_clkn (AB11, 3 MHz) and lbus_rstn (AA13) are not used:
## the bridge samples the bus with its own clock.
## Pull-ups on wrn/rdn keep the bus idle if the Spartan-6 is unconfigured.
## -----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN AD10 IOSTANDARD LVCMOS15 PULLUP TRUE} [get_ports lbus_wrn]
set_property -dict {PACKAGE_PIN Y13  IOSTANDARD LVCMOS15 PULLUP TRUE} [get_ports lbus_rdn]
## One line per pin: XDC is a restricted Tcl subset without loops (for / foreach are
## rejected with [Designutils 20-1307] and the pins would stay unconstrained).
## lbus_do: Kintex-7 -> Spartan-6 read data (1.5 V HP banks, KS_HPIC_*_P).
set_property -dict {PACKAGE_PIN V4   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[0]}]
set_property -dict {PACKAGE_PIN V2   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[1]}]
set_property -dict {PACKAGE_PIN W1   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[2]}]
set_property -dict {PACKAGE_PIN AB1  IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[3]}]
set_property -dict {PACKAGE_PIN Y3   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[4]}]
set_property -dict {PACKAGE_PIN U7   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[5]}]
set_property -dict {PACKAGE_PIN V3   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[6]}]
set_property -dict {PACKAGE_PIN AF10 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[7]}]
set_property -dict {PACKAGE_PIN AC13 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[8]}]
set_property -dict {PACKAGE_PIN AE12 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[9]}]
set_property -dict {PACKAGE_PIN U6   IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[10]}]
set_property -dict {PACKAGE_PIN AE13 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[11]}]
set_property -dict {PACKAGE_PIN AA10 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[12]}]
set_property -dict {PACKAGE_PIN AB12 IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[13]}]
set_property -dict {PACKAGE_PIN AA4  IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[14]}]
set_property -dict {PACKAGE_PIN AE8  IOSTANDARD LVCMOS15 SLEW SLOW DRIVE 4} [get_ports {lbus_do[15]}]
## lbus_di_a: Spartan-6 -> Kintex-7 address / write data (2.5 V HR banks, KS_HRIC_*_P).
set_property -dict {PACKAGE_PIN T22  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[0]}]
set_property -dict {PACKAGE_PIN M24  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[1]}]
set_property -dict {PACKAGE_PIN K25  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[2]}]
set_property -dict {PACKAGE_PIN R26  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[3]}]
set_property -dict {PACKAGE_PIN M25  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[4]}]
set_property -dict {PACKAGE_PIN U17  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[5]}]
set_property -dict {PACKAGE_PIN N26  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[6]}]
set_property -dict {PACKAGE_PIN R16  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[7]}]
set_property -dict {PACKAGE_PIN T20  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[8]}]
set_property -dict {PACKAGE_PIN R22  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[9]}]
set_property -dict {PACKAGE_PIN M21  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[10]}]
set_property -dict {PACKAGE_PIN T24  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[11]}]
set_property -dict {PACKAGE_PIN P23  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[12]}]
set_property -dict {PACKAGE_PIN N21  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[13]}]
set_property -dict {PACKAGE_PIN R21  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[14]}]
set_property -dict {PACKAGE_PIN N18  IOSTANDARD LVCMOS25} [get_ports {lbus_di_a[15]}]

## K_CLK_INH_B (Table 13): driven high = both 200 MHz oscillators enabled,
## as in AIST's reference design (osc_en_b).
set_property -dict {PACKAGE_PIN J8 IOSTANDARD LVCMOS25 DRIVE 4 SLEW SLOW} [get_ports osc_en_b]

## -----------------------------------------------------------------------------
## Kintex-7 GPIO header CN8 (Table 7 pinout, Table 19 pins), 2.5 V:
##   CN8.1  K_HEADER_0 = D19  uart_rxd  (optional adapter TXD; pull-up, so it
##                                       idles high when nothing is connected)
##   CN8.2  K_HEADER_1 = N17  uart_txd  (optional adapter RXD)
##   CN8.3  K_HEADER_2 = N16  trig_out  (scope trigger = STATUS.busy)
##   CN8.11 / CN8.13 GND,  CN8.12 2.5 V (adapter VCCIO),  CN8.14 VIN -- not a logic pin!
## The adapter must drive 2.5 V logic: 3.3 V on an HR pin at VCCO = 2.5 V is
## above the absolute maximum (VCCO + 0.55 V).
## -----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS25 PULLUP TRUE} [get_ports uart_rxd]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS25} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS25} [get_ports trig_out]

## -----------------------------------------------------------------------------
## User LEDs LED1-LED10 (Table 20: K_LED_0..9), 2.5 V. Meaning: sakura_x_top.sv.
## -----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN G20 IOSTANDARD LVCMOS25} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN L19 IOSTANDARD LVCMOS25} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS25} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN H19 IOSTANDARD LVCMOS25} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVCMOS25} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS25} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN T19 IOSTANDARD LVCMOS25} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN T18 IOSTANDARD LVCMOS25} [get_ports {led[7]}]
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS25} [get_ports {led[8]}]
set_property -dict {PACKAGE_PIN H11 IOSTANDARD LVCMOS25} [get_ports {led[9]}]

## -----------------------------------------------------------------------------
## Asynchronous inputs / slow outputs: no meaningful I/O timing relationship.
## SW4 asserts reset asynchronously (release is synchronised in RTL);
## uart_rxd goes through a 2-FF synchroniser in uart_rx.
## -----------------------------------------------------------------------------
set_false_path -from [get_ports pushsw]
set_false_path -from [get_ports uart_rxd]
set_false_path -to   [get_ports uart_txd]
set_false_path -to   [get_ports {led[*]}]
set_false_path -to   [get_ports trig_out]
set_false_path -to   [get_ports osc_en_b]
## Local bus: 3 MHz, asynchronous, synchronised in sakura_lbus_bridge; every
## sample is taken >= 60 ns after the bus settled, lbus_do is read ~1 us after
## it was set.
set_false_path -from [get_ports {lbus_di_a[*] lbus_wrn lbus_rdn}]
set_false_path -to   [get_ports {lbus_do[*]}]

## -----------------------------------------------------------------------------
## Configuration and bitstream
## -----------------------------------------------------------------------------
## Configuration pins (bank 0 / K_CONF_*) are 2.5 V on this board (Table 15).
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 2.5 [current_design]
## Leave unused pins floating instead of Vivado's default weak pull-down: the
## board's own resistors define them (the supervisor's watchdog lines,
## lbus_clkn / lbus_rstn and the rest of the Spartan-6 interconnect, DIP switches).
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]
## The 160T bitstream is ~6.7 MB uncompressed; compression shortens JTAG loads.
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
