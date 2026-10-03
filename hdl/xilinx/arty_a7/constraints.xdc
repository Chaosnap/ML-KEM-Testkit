## Arty A7-35T / A7-100T XDC constraints for pqc_mlkem (top = arty_a7_top)
## Reference: Digilent Arty A7 Reference Manual / Arty-A7-100-Master.xdc

## Clock (100 MHz oscillator)
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports clk_100mhz]
create_clock -period 10.000 -name sys_clk [get_ports clk_100mhz]

## Core clock: MMCM CLKOUT0 (206.00 MHz) is derived automatically from sys_clk.
## The bridge (sys_clk) and the core (clk_core) only talk through axil_cdc,
## whose crossing values are held stable around toggle handshakes.
set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]

## Reset: push button BTN0, active-high (pressed = 1)
set_property -dict {PACKAGE_PIN D9 IOSTANDARD LVCMOS33} [get_ports btn0]

## USB-UART bridge. Names are from the FPGA's point of view:
## uart_txd = FPGA output (board net uart_rxd_out, D10)
## uart_rxd = FPGA input  (board net uart_txd_in,  A9)
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN A9  IOSTANDARD LVCMOS33} [get_ports uart_rxd]

## Green LEDs LD4-LD7
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## Asynchronous inputs / slow outputs: no meaningful I/O timing relationship.
## BTN0 asserts reset asynchronously (release is synchronised in RTL);
## uart_rxd goes through a 2-FF synchroniser in uart_rx.
set_false_path -from [get_ports btn0]
set_false_path -from [get_ports uart_rxd]
set_false_path -to   [get_ports uart_txd]
set_false_path -to   [get_ports {led[*]}]

## Configuration
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
