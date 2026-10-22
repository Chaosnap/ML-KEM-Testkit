# Portable SAKURA-X shell build. No ISE, block design, MIG or external IP needed.
# vivado -mode batch -source scripts/vivado/build_sakura_shell.tcl
# GUI project only: ... -tclargs --project-only
set root [file normalize [file join [file dirname [info script]] ../..]]
set project_dir [file join $root build vivado sakura-shell_mlkem]
set project_name pqc_mlkem_sakura_shell
set top sakura_shell_top
set project_only 0
if {[info exists argv]} {
    foreach arg $argv {
        if {$arg eq "--project-only"} {set project_only 1} else {error "Unknown option: $arg"}
    }
}
# Do not overwrite an existing implementation: reopening preserves GUI edits.
set xpr [file join $project_dir ${project_name}.xpr]
if {[file exists $xpr]} {
    open_project $xpr
} else {
    create_project $project_name $project_dir -part xc7k160tfbg676-1
    add_files -norecurse [concat [glob -nocomplain [file join $root hdl core *.sv]] [glob -nocomplain [file join $root hdl core *.v]]]
    add_files -norecurse [glob [file join $root hdl xilinx sakura_shell *.sv]]
    add_files -fileset constrs_1 -norecurse [file join $root hdl xilinx sakura_shell constraints.xdc]
    set_property top $top [get_filesets sources_1]
    update_compile_order -fileset sources_1
}
if {$project_only} {
    puts "Project ready: $xpr (no synthesis was requested)"
    return
}
# Reset only this generated project's runs for an explicit rebuild.
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "Synthesis failed: inspect $project_dir/${project_name}.runs/synth_1/runme.log"
}
set impl [get_runs impl_1]
set_property STEPS.OPT_DESIGN.ARGS.DIRECTIVE Explore $impl
set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE ExtraTimingOpt $impl
set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true $impl
set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore $impl
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE AggressiveExplore $impl
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS $impl] ne "100%"} {error "Implementation failed; inspect runme.log"}
open_run impl_1
report_timing_summary -file [file join $project_dir timing.rpt]
report_utilization -hierarchical -file [file join $project_dir utilization.rpt]
report_clock_interaction -file [file join $project_dir clocks.rpt]
report_cdc -file [file join $project_dir cdc.rpt]
report_io -file [file join $project_dir pins.rpt]
report_drc -file [file join $project_dir drc.rpt]
set setup [get_timing_paths -setup -max_paths 1]
set hold [get_timing_paths -hold -max_paths 1]
if {![llength $setup] || ![llength $hold]} {error "Missing timing paths; review clock constraints"}
if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} {
    error "Timing NOT met. Do not use this build for acquisition; inspect timing.rpt"
}
set bit [file join [get_property DIRECTORY $impl] ${top}.bit]
if {![file exists $bit]} {error "Bitstream missing: $bit"}
puts "Kintex bitstream: $bit"
puts "Program this file with Vivado Hardware Manager on CN5."
puts "Spartan CN10 needs third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit"
puts "Linux/hardware validation remains necessary: docs/SAKURA_X_SHELL.md"
