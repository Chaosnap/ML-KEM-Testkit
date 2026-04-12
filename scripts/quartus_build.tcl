# quartus_build.tcl — Intel Quartus Prime synthesis and implementation script
#
# Usage:
#   quartus_sh --script scripts/quartus_build.tcl <board> <algorithm>
#
# Example:
#   quartus_sh --script scripts/quartus_build.tcl de10-nano mlkem
#
# Boards: de10-nano, de10-agilex, stratix10-dx
# Algorithms: mlkem, mldsa, slhdsa

package require cmdline

# Parse arguments
if {$argc < 2} {
    puts "Usage: quartus_sh --script quartus_build.tcl <board> <algorithm>"
    exit 1
}

set board_name [lindex $quartus(args) 0]
set algorithm  [lindex $quartus(args) 1]
set project_name "pqc_${algorithm}"
set project_dir "build/quartus/${board_name}_${algorithm}"

# Board-to-device mapping
array set board_devices {
    de10-nano     5CSEBA6U23I7
    de10-agilex   AGFB014R24B2E2V
    stratix10-dx  1SD280PT2F55E1VG
}

array set board_families {
    de10-nano     {Cyclone V}
    de10-agilex   {Agilex 7}
    stratix10-dx  {Stratix 10}
}

if {![info exists board_devices($board_name)]} {
    puts "ERROR: Unknown board '$board_name'"
    exit 1
}

set device $board_devices($board_name)
set family $board_families($board_name)

puts "=== PQC Test Kit — Quartus Build ==="
puts "Board:     $board_name"
puts "Device:    $device"
puts "Family:    $family"
puts "Algorithm: $algorithm"
puts ""

# Create project
file mkdir $project_dir
cd $project_dir

project_new $project_name -overwrite
set_global_assignment -name FAMILY $family
set_global_assignment -name DEVICE $device
set_global_assignment -name TOP_LEVEL_ENTITY pqc_${algorithm}_top

# Add RTL sources — vendor-agnostic cores
foreach f [glob -nocomplain ../../../hdl/core/*.sv ../../../hdl/core/*.v] {
    set_global_assignment -name SYSTEMVERILOG_FILE $f
}

# Add Intel-specific wrappers
set intel_dir "../../../hdl/intel/${board_name}"
if {[file exists $intel_dir]} {
    foreach f [glob -nocomplain ${intel_dir}/*.sv ${intel_dir}/*.v] {
        set_global_assignment -name SYSTEMVERILOG_FILE $f
    }
}

# Add constraints
set sdc_file "${intel_dir}/constraints.sdc"
if {[file exists $sdc_file]} {
    set_global_assignment -name SDC_FILE $sdc_file
}

# Run compilation
puts "--- Running Analysis & Synthesis ---"
execute_module -tool map

puts "--- Running Fitter ---"
execute_module -tool fit

puts "--- Running Timing Analysis ---"
execute_module -tool sta

puts "--- Running Assembler ---"
execute_module -tool asm

project_close

puts ""
puts "=== Build Complete ==="
puts "Output: ${project_dir}/"
