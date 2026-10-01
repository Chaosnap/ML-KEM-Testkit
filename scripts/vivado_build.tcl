# vivado_build.tcl — Xilinx Vivado synthesis and implementation script
#
# Usage:
#   vivado -mode batch -source scripts/vivado_build.tcl -tclargs <board> <algorithm>
#
# From the Vivado GUI Tcl console (any working directory; without arguments
# the default target arty-a7-100t mlkem is built):
#   source -notrace <repo>/scripts/vivado_build.tcl
#
# Paths are resolved from this script's location, so the current directory
# does not matter. Failures raise a Tcl error instead of calling `exit`: in
# batch mode Vivado still exits non-zero, in the GUI the console shows the
# message and Vivado stays open.
#
# Examples:
#   vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem
#   vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-100t mlkem
#
# This script:
#   1. Creates a new Vivado project for the target board
#   2. Adds the vendor-agnostic core RTL and Xilinx-specific wrappers
#   3. Applies board constraints (XDC)
#   4. Selects the correct board-level top module
#   5. Runs synthesis, implementation, and bitstream generation
#   6. Reports utilization, timing, and power

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
# Print the message and abort the script with a Tcl error (not `exit`).
proc fail {msg} {
    puts "ERROR: $msg"
    error $msg
}

# Without -tclargs (e.g. `source` in the GUI) build the default target.
if {$argc >= 2} {
    set board_name [lindex $argv 0]
    set algorithm  [lindex $argv 1]
} else {
    set board_name arty-a7-100t
    set algorithm  mlkem
    puts "No <board> <algorithm> given, using defaults: $board_name $algorithm"
    puts "  (Boards: arty-a7-35t, arty-a7-100t, nexys-a7, zcu102, alveo-u250;"
    puts "   algorithms: mlkem, mldsa, slhdsa)"
}

set repo_root   [file normalize [file join [file dirname [info script]] ..]]
set project_dir "${repo_root}/build/vivado/${board_name}_${algorithm}"

# -----------------------------------------------------------------------------
# Board-to-part mapping
# -----------------------------------------------------------------------------
array set board_parts {
    arty-a7-35t   xc7a35ticsg324-1L
    arty-a7-100t  xc7a100tcsg324-1
    nexys-a7      xc7a200tsbg484-1
    zcu102        xczu9eg-ffvb1156-2-e
    alveo-u250    xcu250-figd2104-2L-e
}

if {![info exists board_parts($board_name)]} {
    fail "Unknown board '$board_name'"
}

set part $board_parts($board_name)

puts "=== PQC Test Kit — Vivado Build ==="
puts "Board:     $board_name"
puts "Part:      $part"
puts "Algorithm: $algorithm"
puts "Repo:      $repo_root"
puts "Project:   $project_dir"
puts ""

# -----------------------------------------------------------------------------
# Create project
# -----------------------------------------------------------------------------
create_project pqc_${algorithm} $project_dir -part $part -force

# -----------------------------------------------------------------------------
# Add vendor-agnostic RTL sources
# -----------------------------------------------------------------------------
set core_sources [glob -nocomplain ${repo_root}/hdl/core/*.sv ${repo_root}/hdl/core/*.v]

if {[llength $core_sources] == 0} {
    fail "No RTL sources found under hdl/core/"
}

add_files -norecurse $core_sources

# -----------------------------------------------------------------------------
# Select board-specific wrapper directory
#
# Arty A7-35T and Arty A7-100T share the same board wrapper/XDC directory:
#   hdl/xilinx/arty_a7/
# -----------------------------------------------------------------------------
if {$board_name eq "arty-a7-35t" || $board_name eq "arty-a7-100t"} {
    set xilinx_dir "${repo_root}/hdl/xilinx/arty_a7"
} else {
    set xilinx_dir "${repo_root}/hdl/xilinx/${board_name}"
}

if {![file isdirectory $xilinx_dir]} {
    fail "Board support directory not found: $xilinx_dir"
}

# Add board-specific wrapper RTL
set board_sources [glob -nocomplain ${xilinx_dir}/*.sv ${xilinx_dir}/*.v]

if {[llength $board_sources] == 0} {
    fail "No board wrapper RTL found in: $xilinx_dir"
}

add_files -norecurse $board_sources

# -----------------------------------------------------------------------------
# Add constraints
# -----------------------------------------------------------------------------
set xdc_file "${xilinx_dir}/constraints.xdc"

if {![file exists $xdc_file]} {
    fail "Constraint file not found: $xdc_file"
}

add_files -fileset constrs_1 -norecurse $xdc_file

# -----------------------------------------------------------------------------
# Select top module
#
# Current arty_a7_top wraps pqc_mlkem_top and provides:
#   - 100 MHz board clock
#   - reset button
#   - USB-UART RX/TX
#   - UART-to-AXI bridge
#   - status LEDs
# Therefore the current Arty board wrapper is ML-KEM-specific.
# -----------------------------------------------------------------------------
if {$board_name eq "arty-a7-35t" || $board_name eq "arty-a7-100t"} {
    if {$algorithm ne "mlkem"} {
        puts "       Use algorithm 'mlkem' or add a board wrapper for '$algorithm'."
        fail "Current hdl/xilinx/arty_a7/arty_a7_top.sv wraps ML-KEM only."
    }
    set top_name "arty_a7_top"
} else {
    set top_name "pqc_${algorithm}_top"
}

set_property top $top_name [get_filesets sources_1]
update_compile_order -fileset sources_1

puts "Board RTL:  $xilinx_dir"
puts "XDC:        $xdc_file"
puts "Top:        $top_name"
puts ""

# -----------------------------------------------------------------------------
# Run synthesis
# -----------------------------------------------------------------------------
puts "--- Running Synthesis ---"
launch_runs synth_1 -jobs 4

if {[catch {wait_on_run synth_1} synth_err]} {
    puts "Vivado message: $synth_err"
    puts "Run directory: [get_property DIRECTORY [get_runs synth_1]]"
    fail "Synthesis run failed."
}

set synth_status [get_property STATUS [get_runs synth_1]]
puts "Synthesis status: $synth_status"

if {$synth_status ne "synth_design Complete!"} {
    puts "Run directory: [get_property DIRECTORY [get_runs synth_1]]"
    fail "Synthesis did not complete successfully."
}

# Reports after synthesis
open_run synth_1
report_utilization    -file ${project_dir}/utilization_synth.rpt
report_utilization    -hierarchical -hierarchical_depth 4 \
                      -file ${project_dir}/utilization_synth_hier.rpt
report_timing_summary -file ${project_dir}/timing_synth.rpt
close_design

# Synthesis warnings, with removed/trimmed logic called out separately so a
# datapath that was optimised away is visible immediately.
set synth_log [file join [get_property DIRECTORY [get_runs synth_1]] runme.log]
set warn_rpt  ${project_dir}/synth_warnings.rpt
if {[file exists $synth_log]} {
    set fin  [open $synth_log r]
    set fout [open $warn_rpt w]
    set n_warn 0
    set n_trim 0
    while {[gets $fin line] >= 0} {
        if {[string match "CRITICAL WARNING:*" $line] || [string match "WARNING:*" $line]} {
            puts $fout $line
            incr n_warn
            # 8-3332 sequential element removed, 8-6014 unused sequential
            # element removed, 8-3848 net has no driver, 8-7129 port unused,
            # 8-3936 register trimmed, 8-3917 port driven by constant.
            if {[regexp {Synth 8-(3332|6014|3848|7129|3936|3917)} $line]} {
                incr n_trim
            }
        }
    }
    close $fin
    close $fout
    puts "Synthesis warnings: $n_warn total, $n_trim removed/trimmed/unconnected (see $warn_rpt)"
}

# -----------------------------------------------------------------------------
# Run implementation through bitstream generation
# -----------------------------------------------------------------------------
puts "--- Running Implementation + Bitstream ---"
launch_runs impl_1 -to_step write_bitstream -jobs 4

if {[catch {wait_on_run impl_1} impl_err]} {
    puts "Vivado message: $impl_err"
    puts "Run directory: [get_property DIRECTORY [get_runs impl_1]]"
    puts "Check runme.log in the directory above for the first real ERROR message."
    fail "Implementation/bitstream run failed."
}

set impl_status [get_property STATUS [get_runs impl_1]]
puts "Implementation status: $impl_status"

# -----------------------------------------------------------------------------
# Reports after implementation
# -----------------------------------------------------------------------------
open_run impl_1
report_utilization    -file ${project_dir}/utilization_impl.rpt
report_utilization    -hierarchical -hierarchical_depth 4 \
                      -file ${project_dir}/utilization_impl_hier.rpt
report_timing_summary -file ${project_dir}/timing_impl.rpt
report_power          -file ${project_dir}/power.rpt

# Console summary: per-instance utilization and timing.
puts ""
puts "=== Hierarchical utilization (post-implementation) ==="
puts [report_utilization -hierarchical -hierarchical_depth 4 -return_string]

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1 -nworst 1] 0]]
set tns 0.0
foreach p [get_timing_paths -setup -max_paths 10000 -slack_lesser_than 0] {
    set tns [expr {$tns + [get_property SLACK $p]}]
}
puts "Timing: WNS = $wns ns, TNS = $tns ns"

# -----------------------------------------------------------------------------
# Final output
# -----------------------------------------------------------------------------
set bitstream_file "${project_dir}/pqc_${algorithm}.runs/impl_1/${top_name}.bit"

puts ""
puts "=== Build Complete ==="
puts "Board:     $board_name"
puts "Part:      $part"
puts "Top:       $top_name"
puts "Bitstream: $bitstream_file"
puts "Reports:   ${project_dir}/"

if {![file exists $bitstream_file]} {
    puts "WARNING: Expected bitstream was not found at:"
    puts "         $bitstream_file"
    puts "         Check the impl_1 run directory and Vivado log."
}
