# Refresh the existing SAKURA-X shell project from Vivado's Tools -> Run Tcl Script.
# Installs run settings only; does not launch or reset a run, or program hardware.
namespace eval sakura_x_config {
    set root [file normalize [file join [file dirname [info script]] ../..]]
    if {[current_project -quiet] eq ""} {
        error "Open pqc_mlkem_sakura_x.xpr before configuring the shell project."
    }
    if {[get_property TOP [get_filesets sources_1]] ne "sakura_x_top"} {
        error "This script is only for the sakura_x_top project."
    }
    set impl [get_runs impl_1]
    set_property STEPS.OPT_DESIGN.ARGS.DIRECTIVE Explore $impl
    set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE ExtraTimingOpt $impl
    set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true $impl
    set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore $impl
    set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE AggressiveExplore $impl
    set_property STEPS.WRITE_BITSTREAM.TCL.PRE \
        [file join $root scripts vivado check_sakura_x_timing.tcl] $impl
    puts "Shell settings saved. Generate Bitstream will check routed setup/hold timing first."
}
