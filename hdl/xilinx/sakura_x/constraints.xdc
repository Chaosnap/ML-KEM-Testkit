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
set_property -dict {PACKAGE_PIN AA3 IOSTANDARD LVDS} [get_ports clk200_p]
set_property -dict {PACKAGE_PIN AA2 IOSTANDARD LVDS} [get_ports clk200_n]
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
## Kintex-7 GPIO header CN8 (Table 7 pinout, Table 19 pins), 2.5 V:
##   CN8.1  K_HEADER_0 = D19  uart_rxd  (FPGA input,  from the adapter's TXD)
##   CN8.2  K_HEADER_1 = N17  uart_txd  (FPGA output, to the adapter's RXD)
##   CN8.3  K_HEADER_2 = N16  trig_out  (scope trigger = STATUS.busy)
##   CN8.11 / CN8.13 GND,  CN8.12 2.5 V (adapter VCCIO),  CN8.14 VIN -- not a logic pin!
## The adapter must drive 2.5 V logic: 3.3 V on an HR pin at VCCO = 2.5 V is
## above the absolute maximum (VCCO + 0.55 V).
## -----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS25} [get_ports uart_rxd]
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

## -----------------------------------------------------------------------------
## Configuration and bitstream
## -----------------------------------------------------------------------------
## Configuration pins (bank 0 / K_CONF_*) are 2.5 V on this board (Table 15).
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 2.5 [current_design]
## Leave unused pins floating instead of Vivado's default weak pull-down: the
## board's own resistors define them (oscillator inhibit K_CLK_INH_B on J8,
## the supervisor's watchdog lines, the Spartan-6 interconnect, DIP switches).
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]
## The 160T bitstream is ~6.7 MB uncompressed; compression shortens JTAG loads.
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
