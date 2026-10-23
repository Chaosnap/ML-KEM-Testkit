# Run on the routed design, BEFORE write_bitstream, in GUI and batch flows.
# Keep reports on failure so the user can inspect paths without CLI commands.
namespace eval sakura_x_timing {
    set root [file normalize [file join [file dirname [info script]] ../..]]
    set report_dir [file join $root build vivado sakura-x_mlkem]
    file mkdir $report_dir
    report_timing_summary -report_unconstrained -file [file join $report_dir timing.rpt]
    report_utilization -hierarchical -file [file join $report_dir utilization.rpt]
    report_clock_interaction -file [file join $report_dir clocks.rpt]
    report_clock_utilization -file [file join $report_dir clock_utilization_report.txt]
    report_cdc -details -file [file join $report_dir cdc.rpt]
    report_io -file [file join $report_dir pins.rpt]
    report_drc -file [file join $report_dir drc.rpt]
    set bus_outputs [get_ports {o_read_data[*] o_addr_ready o_write_data_ready o_read_data_valid}]
    set bus_inputs [get_ports {i_common[*] i_addr_valid i_write_enable i_write_data_valid i_read_data_ready}]
    if {[llength $bus_outputs] != 35 || [llength $bus_inputs] != 36} {
        error "Shell timing gate: unexpected I/O ports; check the top/constraints."
    }
    foreach mode {max min} {
        report_timing -delay_type $mode -to $bus_outputs -max_paths 35 -nworst 1 \
            -path_type full_clock_expanded -file [file join $report_dir shell_outputs_${mode}.rpt]
        report_timing -delay_type $mode -from $bus_inputs -max_paths 36 -nworst 1 \
            -path_type full_clock_expanded -file [file join $report_dir shell_inputs_${mode}.rpt]
    }
    set setup [get_timing_paths -setup -max_paths 1 -sort_by slack]
    set hold [get_timing_paths -hold -max_paths 1 -sort_by slack]
    if {![llength $setup] || ![llength $hold]} {
        error "Shell timing gate: missing timing paths; review clock constraints."
    }
    set wns [get_property SLACK [lindex $setup 0]]
    set whs [get_property SLACK [lindex $hold 0]]
    if {$wns < 0 || $whs < 0} {
        error "Bitstream generation blocked: routed WNS=$wns ns, WHS=$whs ns. Inspect $report_dir/timing.rpt and shell_outputs_max.rpt."
    }
    puts "Routed setup/hold passed: WNS=$wns ns, WHS=$whs ns. Review CDC/I/O budgets and validate on hardware."
}
