## Arty Z7-20 PS-UART variant (top = arty_z7_ps_top). Same PL pins as
## hdl/xilinx/arty_z7/constraints.xdc minus the Pmod UART pins: the host
## link goes through the Zynq PS (on-board USB -> MIO 14/15 -> UART0 ->
## ARM forwarder -> UART1 -> EMIO). DDR / MIO / PS_CLK are dedicated PS pins
## constrained by the PS7 IP itself.
## Pins checked against Digilent's official Arty-Z7-20-Master.xdc.

## Clock: 125 MHz PL system-clock oscillator (sysclk).
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports sysclk]
create_clock -period 8.000 -name sys_clk [get_ports sysclk]

## Core clock: MMCM CLKOUT0 (206.25 MHz), derived from sys_clk. Only
## crosses to sys_clk through axil_cdc.
set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]

## Reset: push button BTN0, active-high (pressed = 1).
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports btn0]

## Oscilloscope trigger / capture-enable output: Pmod JA pin 3 (Y16).
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports trig_out]

## User LEDs LD0-LD3.
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
set_false_path -from [get_ports btn0]
set_false_path -to   [get_ports {led[*]}]
set_false_path -to   [get_ports {rgb_r[*] rgb_g[*] rgb_b[*]}]
set_false_path -to   [get_ports trig_out]

## Configuration.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
