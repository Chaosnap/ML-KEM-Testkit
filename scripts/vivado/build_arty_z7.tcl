# build_arty_z7.tcl — Xilinx Vivado synthesis and implementation
# script for the Digilent Arty Z7-10 / Arty Z7-20 (Zynq-7000 APSoC).
#
# Deliberately a STANDALONE script, not a board case inside
# build_arty_a7.tcl (which stays Arty-A7-family-only, see its own header
# comment) -- this file shares no Tcl state, no project directory and no
# RTL/XDC files with that script or with any Arty A7 build. Building for
# Z7 can never perturb an existing A7 build, and vice versa.
#
# Only the vendor-agnostic hdl/core/*.sv is shared with the Arty A7 build
# (by design -- see hdl/xilinx/arty_z7/arty_z7_top.sv's header comment: this
# is a pure-PL design, no PS7, so the same core RTL applies unchanged). The
# board wrapper (arty_z7_top.sv) and constraints (constraints.xdc) are new,
# separate files under hdl/xilinx/arty_z7/.
#
# Usage:
#   vivado -mode batch -source scripts/vivado/build_arty_z7.tcl -tclargs <algorithm> <part>
#   (both arguments optional; defaults: mlkem, xc7z020clg400-1 i.e. Z7-20)
#
# From the Vivado GUI Tcl console (any working directory):
#   source -notrace <repo>/scripts/vivado/build_arty_z7.tcl
#
# Examples:
#   vivado -mode batch -source scripts/vivado/build_arty_z7.tcl -tclargs mlkem
#   vivado -mode batch -source scripts/vivado/build_arty_z7.tcl -tclargs mlkem xc7z010clg400-1  ; # Z7-10 instead
#
# This script:
#   1. Creates a new Vivado project for the Arty Z7, in its own project dir
#   2. Adds the vendor-agnostic core RTL (hdl/core/) and the Arty Z7 wrapper
#      (hdl/xilinx/arty_z7/)
#   3. Applies hdl/xilinx/arty_z7/constraints.xdc
#   4. Runs synthesis, implementation, and bitstream generation (same
#      timing-driven directives as build_arty_a7.tcl's Arty A7 build, since
#      this core's timing margin is tight on any -1 speed-grade 7-series part)
#   5. Reports utilization, timing, and power
#
# PIN-CONFIDENCE REMINDER: hdl/xilinx/arty_z7/constraints.xdc's own header
# says exactly which pins are best-effort and need checking against your
# actual board before you trust a physical connection to them. This script
# does not change that -- a clean build here only means the design
# synthesized, placed, and routed; it says nothing about whether every pin
# constraint matches your physical board.

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
    fail "hdl/xilinx/arty_z7/arty_z7_top.sv wraps ML-KEM only -- pass 'mlkem' or add a board wrapper for '$algorithm'."
}

if {$argc >= 2} {
    set part [lindex $argv 1]
} else {
    set part xc7z020clg400-1
    puts "No <part> given, using default: $part (Arty Z7-20; pass xc7z010clg400-1 for Z7-10)"
}

set board_name   "arty-z7"
set repo_root    [file normalize [file join [file dirname [info script]] .. ..]]
set xilinx_dir   "${repo_root}/hdl/xilinx/arty_z7"
# Own, separate project directory -- never overlaps with build_arty_a7.tcl's
# build/vivado/arty-a7-*_mlkem output, even if both are built back to back.
set project_dir  "${repo_root}/build/vivado/${board_name}_${algorithm}"

puts "=== PQC Test Kit — Vivado Build (Arty Z7, standalone script) ==="
puts "Board:     Arty Z7 ($part)"
puts "Part:      $part"
puts "Algorithm: $algorithm"
puts "Repo:      $repo_root"
puts "Project:   $project_dir"
puts ""

# -----------------------------------------------------------------------------
# Create project
# -----------------------------------------------------------------------------
create_project pqc_${algorithm}_arty_z7 $project_dir -part $part -force

# -----------------------------------------------------------------------------
# Add vendor-agnostic RTL sources (shared, read-only, with the Arty A7 build)
# -----------------------------------------------------------------------------
set core_sources [glob -nocomplain ${repo_root}/hdl/core/*.sv ${repo_root}/hdl/core/*.v]

if {[llength $core_sources] == 0} {
    fail "No RTL sources found under hdl/core/"
}

add_files -norecurse $core_sources

# -----------------------------------------------------------------------------
# Add the Arty Z7 board wrapper + constraints (both new, Z7-only files)
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
set top_name "arty_z7_top"
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
# Same timing-driven directives as build_arty_a7.tcl's Arty A7 build: the
# ML-KEM core's timing margin is tight (observed WNS as low as ~9 ps on a
# -1 speed-grade Artix-7 part) regardless of which 7-series/Zynq-7000 part
# it lands on, so start from the aggressive strategy rather than the default
# one and tighten further by hand if this part's WNS still comes in near zero.
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
    puts "NOTE: WNS is positive but under 100 ps -- timing is met but with very little margin;"
    puts "      a different seed, a part-speed-grade change, or future RTL changes could easily"
    puts "      push this negative. Consider report_timing_summary -delay_type min_max for detail."
}

# -----------------------------------------------------------------------------
# Final output
# -----------------------------------------------------------------------------
set bitstream_file "${project_dir}/pqc_${algorithm}_arty_z7.runs/impl_1/${top_name}.bit"

puts ""
puts "=== Build Complete ==="
puts "Board:     Arty Z7 ($part)"
puts "Part:      $part"
puts "Top:       $top_name"
puts "Bitstream: $bitstream_file"
puts "Reports:   ${project_dir}/"

if {![file exists $bitstream_file]} {
    puts "WARNING: Expected bitstream was not found at:"
    puts "         $bitstream_file"
    puts "         Check the impl_1 run directory and Vivado log."
}
