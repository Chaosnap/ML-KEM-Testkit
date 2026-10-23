# Portable SAKURA-X shell build. No ISE, block design, MIG or external IP needed.
# vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
# GUI project only: ... -tclargs --project-only
set root [file normalize [file join [file dirname [info script]] ../..]]
set project_dir [file join $root build vivado sakura-x_mlkem]
set project_name pqc_mlkem_sakura_x
set top sakura_x_top
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
    add_files -norecurse [glob [file join $root hdl xilinx sakura_x *.sv]]
    add_files -fileset constrs_1 -norecurse [file join $root hdl xilinx sakura_x constraints.xdc]
    set_property top $top [get_filesets sources_1]
    update_compile_order -fileset sources_1
}
# Also install the timing gate for --project-only and previously opened projects.
source [file join $root scripts vivado configure_sakura_x.tcl]
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
# Route and inspect timing before creating a programmable file. The pre-bitstream
# hook repeats this gate if Generate Bitstream is later clicked in the GUI.
launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run impl_1
if {[get_property STATUS $impl] ne "route_design Complete!"} {
    error "Routing failed; inspect the implementation runme.log"
}
open_run impl_1
source [file join $root scripts vivado check_sakura_x_timing.tcl]
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property STATUS $impl] ne "write_bitstream Complete!"} {
    error "Bitstream generation failed; inspect the implementation runme.log"
}
set bit [file join [get_property DIRECTORY $impl] ${top}.bit]
if {![file exists $bit]} {error "Bitstream missing: $bit"}
puts "Kintex bitstream: $bit"
puts "Program this file with Vivado Hardware Manager on CN5."
puts "Spartan CN10 needs third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit"
puts "Linux/hardware validation remains necessary: docs/SAKURA_X.md"
