# build_arty_z7_ps.tcl — Arty Z7-20, host link over the ON-BOARD USB port.
#
# Variant of scripts/vivado/build_arty_z7.tcl (pure PL, external USB-UART
# on Pmod JA), which is left unchanged. This one adds the Zynq Processing
# System so the on-board USB serial (wired only to PS MIO 14/15) can reach
# the PL UART bridge:
#
#   on-board USB -> PS UART0 (MIO 14/15) -> ARM forwarder -> PS UART1 -> EMIO -> PL
#
# The ARM forwarder is sw/arty_z7_uart_fwd (build it on the host with
# `make -C sw/arty_z7_uart_fwd`), loaded together with this bitstream by
# `uv run scripts/board/boot_arty_z7_ps.py` after every power-up (or by
# scripts/vivado/load_arty_z7_ps.tcl where xsdb can reach the ARM DAP).
#
# Usage:
#   vivado -mode batch -source scripts/vivado/build_arty_z7_ps.tcl [-tclargs mlkem]
# From the Vivado GUI Tcl console:
#   source -notrace <repo>/scripts/vivado/build_arty_z7_ps.tcl
#
# Shares only hdl/core/*.sv with the other builds. Own project directory
# (build/vivado/arty-z7-ps_mlkem), own board wrapper and XDC
# (hdl/xilinx/arty_z7_ps/), so it never touches an Arty A7 or pure-PL
# Arty Z7 build.
#
# PS7 configuration = Digilent's official Arty Z7-20 board preset (vendored,
# MIT licensed, in hdl/xilinx/arty_z7_ps/board_files/) plus:
#   UART1 on EMIO (to the PL), M_AXI_GP0 off, FCLK0 / FCLK_RESET0 ports off.
# UART0 stays on MIO 14/15 at 115200; DDR keeps the preset unchanged.
#
# Outputs (stable paths, used by scripts/board/boot_arty_z7_ps.py / load_arty_z7_ps.tcl):
#   build/vivado/arty-z7-ps_mlkem/out/arty_z7_ps_top.bit
#   build/vivado/arty-z7-ps_mlkem/out/ps7_init.tcl

proc fail {msg} {
    puts "ERROR: $msg"
    error $msg
}

if {$argc >= 1} {
    set algorithm [lindex $argv 0]
} else {
    set algorithm mlkem
}
if {$algorithm ne "mlkem"} {
    fail "hdl/xilinx/arty_z7_ps/arty_z7_ps_top.sv wraps ML-KEM only -- pass 'mlkem'."
}

set part        xc7z020clg400-1
set board_part  digilentinc.com:arty-z7-20:part0:1.1
set repo_root   [file normalize [file join [file dirname [info script]] .. ..]]
set xilinx_dir  "${repo_root}/hdl/xilinx/arty_z7_ps"
set project_dir "${repo_root}/build/vivado/arty-z7-ps_${algorithm}"
set out_dir     "${project_dir}/out"
set top_name    "arty_z7_ps_top"

puts "=== PQC Test Kit — Vivado Build (Arty Z7-20, PS UART via on-board USB) ==="
puts "Part:      $part"
puts "Board:     $board_part"
puts "Project:   $project_dir"
puts ""

# Digilent's preset carries two board-measured DDR DQS-to-clock delays that
# are slightly negative (-0.009 / -0.033 ns); the PS7 IP flags those as
# CRITICAL WARNING PSU-1 / PSU-2 every time it re-validates its parameters.
# They are Digilent's own calibration values, shipped in every stock Arty Z7
# design, and this design never uses DDR (the ARM program runs from on-chip
# memory). Downgraded so the critical-warning count stays meaningful.
set_msg_config -id {PSU-1} -new_severity {WARNING}
set_msg_config -id {PSU-2} -new_severity {WARNING}

set_param board.repoPaths [list "${xilinx_dir}/board_files"]
create_project pqc_${algorithm}_arty_z7_ps $project_dir -part $part -force
set_property board_part $board_part [current_project]

# -----------------------------------------------------------------------------
# Sources: vendor-agnostic core + this variant's wrapper and constraints
# -----------------------------------------------------------------------------
set core_sources [glob -nocomplain ${repo_root}/hdl/core/*.sv ${repo_root}/hdl/core/*.v]
if {[llength $core_sources] == 0} { fail "No RTL sources found under hdl/core/" }
add_files -norecurse $core_sources

set board_sources [glob -nocomplain ${xilinx_dir}/*.sv]
if {[llength $board_sources] == 0} { fail "No board wrapper RTL found in: $xilinx_dir" }
add_files -norecurse $board_sources

set xdc_file "${xilinx_dir}/constraints.xdc"
if {![file exists $xdc_file]} { fail "Constraint file not found: $xdc_file" }
add_files -fileset constrs_1 -norecurse $xdc_file

# -----------------------------------------------------------------------------
# Zynq PS block design (zynq_ps -> zynq_ps_wrapper)
# -----------------------------------------------------------------------------
create_bd_design zynq_ps
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps7
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0          {0} \
    CONFIG.PCW_EN_CLK0_PORT           {0} \
    CONFIG.PCW_EN_RST0_PORT           {0} \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_UART1_UART1_IO         {EMIO} \
] $ps7
make_bd_intf_pins_external [get_bd_intf_pins ps7/UART_1]

# Fail early if the preset did not land where the ARM forwarder expects it.
foreach {param expect} {
    PCW_UART0_PERIPHERAL_ENABLE 1
    PCW_UART0_UART0_IO          {MIO 14 .. 15}
    PCW_UART1_UART1_IO          EMIO
    PCW_UART0_BAUD_RATE         115200
    PCW_UART1_BAUD_RATE         115200
    PCW_UART_PERIPHERAL_FREQMHZ 100
} {
    set got [get_property CONFIG.$param $ps7]
    if {$got ne $expect} { fail "PS7 $param = '$got', expected '$expect'" }
}

validate_bd_design
save_bd_design
set bd_file [get_files zynq_ps.bd]
add_files -norecurse [make_wrapper -files $bd_file -top]
generate_target all $bd_file

set_property top $top_name [get_filesets sources_1]
update_compile_order -fileset sources_1

# -----------------------------------------------------------------------------
# Synthesis
# -----------------------------------------------------------------------------
puts "--- Running Synthesis ---"
launch_runs synth_1 -jobs 4
if {[catch {wait_on_run synth_1} synth_err]} {
    puts "Vivado message: $synth_err"
    fail "Synthesis run failed (see [get_property DIRECTORY [get_runs synth_1]])."
}
set synth_status [get_property STATUS [get_runs synth_1]]
puts "Synthesis status: $synth_status"
if {$synth_status ne "synth_design Complete!"} {
    fail "Synthesis did not complete (see [get_property DIRECTORY [get_runs synth_1]])."
}

# -----------------------------------------------------------------------------
# Implementation + bitstream (same timing-driven directives as the other builds)
# -----------------------------------------------------------------------------
puts "--- Running Implementation + Bitstream ---"
set impl [get_runs impl_1]
set_property STEPS.OPT_DESIGN.ARGS.DIRECTIVE                Explore           $impl
set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE              ExtraTimingOpt    $impl
set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED               true              $impl
set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE           AggressiveExplore $impl
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE              AggressiveExplore $impl
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED    true              $impl
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore $impl
launch_runs impl_1 -to_step write_bitstream -jobs 4
if {[catch {wait_on_run impl_1} impl_err]} {
    puts "Vivado message: $impl_err"
    fail "Implementation/bitstream run failed (see [get_property DIRECTORY [get_runs impl_1]]/runme.log)."
}
puts "Implementation status: [get_property STATUS [get_runs impl_1]]"

open_run impl_1
report_utilization    -file ${project_dir}/utilization_impl.rpt
report_timing_summary -file ${project_dir}/timing_impl.rpt
report_timing -max_paths 30 -nworst 1 -unique_pins -sort_by slack \
              -file ${project_dir}/timing_paths_impl.rpt
report_power          -file ${project_dir}/power.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1 -nworst 1] 0]]
set whs [get_property SLACK [lindex [get_timing_paths -hold  -max_paths 1 -nworst 1] 0]]
puts "Timing: WNS = $wns ns, WHS = $whs ns"
if {$wns < 0 || $whs < 0} {
    puts "WARNING: timing NOT met -- see timing_paths_impl.rpt."
}

# -----------------------------------------------------------------------------
# Stable output copies for scripts/board/boot_arty_z7_ps.py / load_arty_z7_ps.tcl
# -----------------------------------------------------------------------------
file mkdir $out_dir
set bit_src "${project_dir}/pqc_${algorithm}_arty_z7_ps.runs/impl_1/${top_name}.bit"
if {![file exists $bit_src]} { fail "Bitstream not found: $bit_src" }
file copy -force $bit_src "${out_dir}/${top_name}.bit"

set init_src [glob -nocomplain ${project_dir}/pqc_${algorithm}_arty_z7_ps.gen/sources_1/bd/zynq_ps/ip/*/ps7_init.tcl]
if {[llength $init_src] != 1} { fail "ps7_init.tcl not found (got: $init_src)" }
file copy -force [lindex $init_src 0] "${out_dir}/ps7_init.tcl"

puts ""
puts "=== Build Complete ==="
puts "Bitstream:  ${out_dir}/${top_name}.bit"
puts "PS init:    ${out_dir}/ps7_init.tcl"
puts "Next:       make -C sw/arty_z7_uart_fwd   (on the host, once)"
puts "            uv run scripts/board/boot_arty_z7_ps.py   (on the host, after every power-up)"
