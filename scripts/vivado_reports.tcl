# vivado_reports.tcl - Non-project synth + implementation for resource /
# timing measurements of the ML-KEM core (docs/REFINE_LOG.md).
#
# Usage (from the repository root):
#   vivado -mode batch -nolog -nojournal -source scripts/vivado_reports.tcl \
#          -tclargs <out_dir> [clock_period_ns]
#
# Builds arty_a7_top for xc7a100tcsg324-1 with the default synthesis and
# implementation directives (no bitstream) and writes to <out_dir>:
#   utilization_hier.rpt     report_utilization -hierarchical
#   utilization_u_mlkem.rpt  report_utilization -cells [get_cells u_mlkem]
#   timing_summary.rpt       report_timing_summary
#   summary.txt              period / WNS / WHS one-liners
#
# clock_period_ns (default 10.000) overrides the XDC sys_clk period; use 5
# only to measure Fmax = 1 / (5 - WNS). The XDC itself is never changed.

if {$argc < 1} {
    puts "usage: -tclargs <out_dir> \[clock_period_ns\]"
    exit 1
}
set out_dir [file normalize [lindex $argv 0]]
set period  [expr {$argc > 1 ? [lindex $argv 1] : 10.0}]
set root    [file normalize [file join [file dirname [info script]] ..]]
file mkdir $out_dir

set_param general.maxThreads 8
set part xc7a100tcsg324-1

read_verilog -sv [glob $root/hdl/core/*.sv]
read_verilog -sv [glob $root/hdl/xilinx/arty_a7/*.sv]
read_xdc $root/hdl/xilinx/arty_a7/constraints.xdc

synth_design -top arty_a7_top -part $part
if {$period != 10.0} {
    create_clock -period $period -name sys_clk [get_ports clk_100mhz]
}
opt_design
place_design
phys_opt_design
route_design

report_utilization -hierarchical -file $out_dir/utilization_hier.rpt
report_utilization -cells [get_cells u_mlkem] -file $out_dir/utilization_u_mlkem.rpt
report_timing_summary -max_paths 10 -file $out_dir/timing_summary.rpt

set wns [get_property SLACK [get_timing_paths -setup -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -hold -max_paths 1]]
set f [open $out_dir/summary.txt w]
puts $f "period_ns $period"
puts $f "wns_ns $wns"
puts $f "whs_ns $whs"
puts $f "fmax_mhz [format %.1f [expr {1000.0 / ($period - $wns)}]]"
close $f
puts "=== period $period ns  WNS $wns ns  WHS $whs ns ==="
