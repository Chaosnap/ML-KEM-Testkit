## Arty Z7-10 / Arty Z7-20 (Zynq-7000) XDC constraints for pqc_mlkem
## (top = arty_z7_top). Reference: Digilent Arty Z7 Reference Manual /
## Arty-Z7-20-Master.xdc.
##
## Every PACKAGE_PIN below was checked (2026-10-14) against Digilent's
## official Arty-Z7-20-Master.xdc (github.com/Digilent/digilent-xdc):
## sysclk H16, BTN0 D19, LD0-3 R14/P14/N16/M14, Pmod JA pin 1/2/3 =
## Y18/Y19/Y16. The design has also been built and programmed onto a real
## Arty Z7-20 (clock confirmed at 125 MHz -> 206.25 MHz core).

## Clock: 125 MHz PL system-clock oscillator (sysclk).
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports sysclk]
create_clock -period 8.000 -name sys_clk [get_ports sysclk]

## Core clock: MMCM CLKOUT0 (206.25 MHz) is derived automatically from sys_clk.
## The bridge (sysclk) and the core (clk_core) only talk through axil_cdc,
## whose crossing values are held stable around toggle handshakes.
set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]

## Reset: push button BTN0, active-high (pressed = 1).
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports btn0]

## UART, via an EXTERNAL 3.3 V USB-UART adapter on Pmod JA -- NOT the
## on-board USB-micro port (that port's UART only reaches the Zynq PS's MIO
## pins, not the PL fabric; see arty_z7_top.sv's header comment).
##   JA pin 1 (Y18) uart_txd = FPGA output -> adapter RXD
##   JA pin 2 (Y19) uart_rxd = FPGA input  <- adapter TXD
##   JA pin 5 or 11 = GND    -> adapter GND (leave the adapter's VCC unconnected)
## The adapter must use 3.3 V logic levels: these are LVCMOS33 pins.
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports uart_rxd]

## Oscilloscope trigger / capture-enable output: Pmod JA pin 3 (Y16). Same
## role as hdl/xilinx/arty_a7/constraints.xdc's trig_out.
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports trig_out]

## User LEDs LD0-LD3 (the 4 basic green LEDs, not the 2 RGB LEDs).
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## RGB LEDs LD4, LD5 (Digilent Arty-Z7-20-Master.xdc led4/led5 _r/_g/_b).
## rgb_*[0] = LD4: power-reset, rgb_*[1] = LD5: host link + completion notice.
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports {rgb_r[0]}]
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS33} [get_ports {rgb_g[0]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports {rgb_b[0]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33} [get_ports {rgb_r[1]}]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33} [get_ports {rgb_g[1]}]
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33} [get_ports {rgb_b[1]}]

## Asynchronous inputs / slow outputs: no meaningful I/O timing relationship.
## BTN0 asserts reset asynchronously (release is synchronised in RTL);
## uart_rxd goes through a 2-FF synchroniser in uart_rx.
set_false_path -from [get_ports btn0]
set_false_path -from [get_ports uart_rxd]
set_false_path -to   [get_ports uart_txd]
set_false_path -to   [get_ports {led[*]}]
set_false_path -to   [get_ports {rgb_r[*] rgb_g[*] rgb_b[*]}]
set_false_path -to   [get_ports trig_out]

## Configuration. No PS7 is instantiated in this design (see arty_z7_top.sv's
## header comment) -- the PL fabric is configured exactly like a plain
## 7-series FPGA, same properties as hdl/xilinx/arty_a7/constraints.xdc.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
