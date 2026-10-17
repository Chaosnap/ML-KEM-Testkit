# build_sakura_x.tcl — Xilinx Vivado synthesis and implementation script
# for the SAKURA-X (SASEBO-GIII) side-channel evaluation board.
#
# Targets the board's cryptographic FPGA, the Kintex-7 XC7K160T-1FBG676C
# (U11). The Spartan-6 control FPGA is not touched (Vivado does not support
# Spartan-6 anyway): the host reaches the ML-KEM core through an external
# 2.5 V USB-UART adapter on the Kintex-7 header CN8 -- see
# hdl/xilinx/sakura_x/sakura_x_top.sv.
#
# A STANDALONE script, like build_arty_z7.tcl: own project directory
# (build/vivado/sakura-x_mlkem), own board wrapper and XDC under
# hdl/xilinx/sakura_x/, no shared Tcl state. Building for SAKURA-X can never
# perturb an Arty A7 / Z7 build, and vice versa. Only hdl/core/*.sv is
# shared, read-only.
#
# Usage:
#   vivado -mode batch -source scripts/vivado/build_sakura_x.tcl [-tclargs <algorithm> <part>]
#   (both optional; defaults: mlkem, xc7k160tfbg676-1)
#
# From the Vivado GUI Tcl console (any working directory):
#   source -notrace <repo>/scripts/vivado/build_sakura_x.tcl
#
# Paths are resolved from this script's location. Failures raise a Tcl error
# instead of calling `exit` (batch mode still exits non-zero; the GUI stays open).
#
# SAKURA-X boards were also sold with an XC7K70T or XC7K325T in the same
# FBG676 package; pass that part as the second argument. The pins in
# constraints.xdc are package pins, so they carry over, but check the
# resource and timing reports for the other die.

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
proc fail {msg} {
    puts "ERROR: $msg"
    error $msg
}

if {$argc >= 1} {
    set algorithm [lindex $argv 0]
} else {
    set algorithm mlkem
    puts "No <algorithm> given, using default: $algorithm"
}
if {$algorithm ne "mlkem"} {
    fail "hdl/xilinx/sakura_x/sakura_x_top.sv wraps ML-KEM only -- pass 'mlkem' or add a board wrapper for '$algorithm'."
}

if {$argc >= 2} {
    set part [lindex $argv 1]
} else {
    set part xc7k160tfbg676-1
    puts "No <part> given, using default: $part (SAKURA-X / SASEBO-GIII Kintex-7 160T)"
}

set board_name   "sakura-x"
set repo_root    [file normalize [file join [file dirname [info script]] .. ..]]
set xilinx_dir   "${repo_root}/hdl/xilinx/sakura_x"
set project_dir  "${repo_root}/build/vivado/${board_name}_${algorithm}"
set project_name "pqc_${algorithm}_sakura_x"

puts "=== PQC Test Kit — Vivado Build (SAKURA-X, standalone script) ==="
puts "Board:     SAKURA-X / SASEBO-GIII (Kintex-7 main FPGA)"
puts "Part:      $part"
puts "Algorithm: $algorithm"
puts "Repo:      $repo_root"
puts "Project:   $project_dir"
puts ""

# -----------------------------------------------------------------------------
# Create project
# -----------------------------------------------------------------------------
create_project $project_name $project_dir -part $part -force

# -----------------------------------------------------------------------------
# Add vendor-agnostic RTL sources (shared, read-only, with the Arty builds)
# -----------------------------------------------------------------------------
set core_sources [glob -nocomplain ${repo_root}/hdl/core/*.sv ${repo_root}/hdl/core/*.v]

if {[llength $core_sources] == 0} {
    fail "No RTL sources found under hdl/core/"
}

add_files -norecurse $core_sources

# -----------------------------------------------------------------------------
# Add the SAKURA-X board wrapper + constraints
# -----------------------------------------------------------------------------
if {![file isdirectory $xilinx_dir]} {
    fail "Board support directory not found: $xilinx_dir"
}

set board_sources [glob -nocomplain ${xilinx_dir}/*.sv ${xilinx_dir}/*.v]

if {[llength $board_sources] == 0} {
    fail "No board wrapper RTL found in: $xilinx_dir"
}

add_files -norecurse $board_sources

set xdc_file "${xilinx_dir}/constraints.xdc"

if {![file exists $xdc_file]} {
    fail "Constraint file not found: $xdc_file"
}

add_files -fileset constrs_1 -norecurse $xdc_file

# -----------------------------------------------------------------------------
# Select top module
# -----------------------------------------------------------------------------
set top_name "sakura_x_top"
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
# Same timing-driven directives as the Arty builds. A Kintex-7 -1 is faster
# than the Artix-7 / Zynq-7000 -1 parts the core was closed on, but keep the
# aggressive strategy so the WNS figures stay comparable between boards.
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
report_timing -max_paths 30 -nworst 1 -unique_pins -sort_by slack \
              -file ${project_dir}/timing_paths_impl.rpt
report_power          -file ${project_dir}/power.rpt
# Package pins as placed: compare against hdl/xilinx/sakura_x/constraints.xdc
# before connecting anything to CN8.
report_io             -file ${project_dir}/io_impl.rpt

puts ""
puts "=== Hierarchical utilization (post-implementation) ==="
puts [report_utilization -hierarchical -hierarchical_depth 4 -return_string]

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1 -nworst 1] 0]]
set tns 0.0
foreach p [get_timing_paths -setup -max_paths 10000 -slack_lesser_than 0] {
    set tns [expr {$tns + [get_property SLACK $p]}]
}
puts "Timing: WNS = $wns ns, TNS = $tns ns"
if {$wns < 0} {
    puts "WARNING: negative WNS -- timing NOT met. See timing_paths_impl.rpt for the worst paths."
} elseif {$wns < 0.100} {
    puts "NOTE: WNS is positive but under 100 ps -- timing is met with very little margin."
}

# -----------------------------------------------------------------------------
# Final output
# -----------------------------------------------------------------------------
set bitstream_file "${project_dir}/${project_name}.runs/impl_1/${top_name}.bit"

puts ""
puts "=== Build Complete ==="
puts "Board:     SAKURA-X / SASEBO-GIII"
puts "Part:      $part"
puts "Top:       $top_name"
puts "Bitstream: $bitstream_file"
puts "Reports:   ${project_dir}/"
puts "Program:   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs $bitstream_file"

if {![file exists $bitstream_file]} {
    puts "WARNING: Expected bitstream was not found at:"
    puts "         $bitstream_file"
    puts "         Check the impl_1 run directory and Vivado log."
}
