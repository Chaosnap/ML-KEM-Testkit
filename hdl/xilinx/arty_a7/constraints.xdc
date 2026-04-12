## Arty A7-35T / A7-100T XDC constraints for pqc_mlkem
## Reference: Digilent Arty A7 Reference Manual

## Clock (100 MHz oscillator)
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports clk_100mhz]
create_clock -period 10.000 -name sys_clk [get_ports clk_100mhz]

## Reset (active-low, directly from push button BTN0)
set_property -dict {PACKAGE_PIN D9 IOSTANDARD LVCMOS33} [get_ports rst_n_btn]

## USB-UART bridge (directly on-board, active through USB connection)
set_property -dict {PACKAGE_PIN A9  IOSTANDARD LVCMOS33} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports uart_rxd]

## LEDs (accent LEDs, accent color only - accent = green for standard Arty)
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## Timing constraints
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
