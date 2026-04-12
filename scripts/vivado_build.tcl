# vivado_build.tcl — Xilinx Vivado synthesis and implementation script
#
# Usage:
#   vivado -mode batch -source scripts/vivado_build.tcl -tclargs <board> <algorithm>
#
# Example:
#   vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem
#
# This script:
#   1. Creates a new Vivado project for the target board
#   2. Adds the vendor-agnostic core RTL and Xilinx-specific wrappers
#   3. Applies board constraints (XDC)
#   4. Runs synthesis, implementation, and bitstream generation
#   5. Reports utilization and timing

# Parse arguments
if {$argc < 2} {
    puts "Usage: vivado -mode batch -source vivado_build.tcl -tclargs <board> <algorithm>"
    puts "  Boards: arty-a7-35t, arty-a7-100t, nexys-a7, zcu102, alveo-u250"
    puts "  Algorithms: mlkem, mldsa, slhdsa"
    exit 1
}

set board_name [lindex $argv 0]
set algorithm  [lindex $argv 1]
set project_dir "build/vivado/${board_name}_${algorithm}"

# Board-to-part mapping
array set board_parts {
    arty-a7-35t   xc7a35ticsg324-1L
    arty-a7-100t  xc7a100tcsg324-1
    nexys-a7      xc7a200tsbg484-1
    zcu102        xczu9eg-ffvb1156-2-e
    alveo-u250    xcu250-figd2104-2L-e
}

if {![info exists board_parts($board_name)]} {
    puts "ERROR: Unknown board '$board_name'"
    exit 1
}

set part $board_parts($board_name)
puts "=== PQC Test Kit — Vivado Build ==="
puts "Board:     $board_name"
puts "Part:      $part"
puts "Algorithm: $algorithm"
puts "Project:   $project_dir"
puts ""

# Create project
create_project pqc_${algorithm} $project_dir -part $part -force

# Add RTL sources — vendor-agnostic cores
add_files -norecurse [glob -nocomplain hdl/core/*.sv hdl/core/*.v]

# Add Xilinx-specific wrappers
set xilinx_dir "hdl/xilinx/${board_name}"
if {[file exists $xilinx_dir]} {
    add_files -norecurse [glob -nocomplain ${xilinx_dir}/*.sv ${xilinx_dir}/*.v]
}

# Add constraints
set xdc_file "hdl/xilinx/${board_name}/constraints.xdc"
if {[file exists $xdc_file]} {
    add_files -fileset constrs_1 -norecurse $xdc_file
}

# Set top module based on algorithm
set_property top pqc_${algorithm}_top [current_fileset]

# Run synthesis
puts "--- Running Synthesis ---"
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Check synthesis status
if {[get_property STATUS [get_runs synth_1]] != "synth_design Complete!"} {
    puts "ERROR: Synthesis failed"
    exit 1
}

# Report utilization after synthesis
open_run synth_1
report_utilization -file ${project_dir}/utilization_synth.rpt
report_timing_summary -file ${project_dir}/timing_synth.rpt

# Run implementation
puts "--- Running Implementation ---"
launch_runs impl_1 -jobs 4
wait_on_run impl_1

# Report utilization after implementation
open_run impl_1
report_utilization -file ${project_dir}/utilization_impl.rpt
report_timing_summary -file ${project_dir}/timing_impl.rpt
report_power -file ${project_dir}/power.rpt

# Generate bitstream
puts "--- Generating Bitstream ---"
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

puts ""
puts "=== Build Complete ==="
puts "Bitstream: ${project_dir}/pqc_${algorithm}.runs/impl_1/pqc_${algorithm}_top.bit"
puts "Reports:   ${project_dir}/"
