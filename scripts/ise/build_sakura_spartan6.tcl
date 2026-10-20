# Build AIST's USB/local-bus controller with ISE 14.7 (NOT Vivado).
# Usage: xtclsh scripts/ise/build_sakura_spartan6.tcl ?reference-package-directory?
# The original project/UCF are copied into build/; the reference is not edited.
set repo_root [file normalize [file join [file dirname [info script]] .. ..]]
if {$argc > 1} {
    error "Usage: xtclsh scripts/ise/build_sakura_spartan6.tcl ?reference-package-directory?"
}
if {![llength [info commands project]]} {
    error "Run this script with ISE 14.7 xtclsh, not Vivado or plain tclsh."
}
set reference_dir [file join $repo_root third_party aist_sasebo_giii]
if {$argc == 1} { set reference_dir [file normalize [lindex $argv 0]] }
set source_dir [file join $reference_dir sasebo_giii_ctrl]
set files {chip_sasebo_giii_ctrl.v ctrl_ftdi_usb.v ctrl_lbus.v syncfifo_8x31.v pin_sasebo_giii_sp6.ucf sasebo_giii_ctrl/sasebo_giii_ctrl.xise}
foreach name $files {
    if {![file isfile [file join $source_dir $name]]} {
        error "Missing AIST reference file: [file join $source_dir $name]"
    }
}
set build_dir [file join $repo_root build ise sakura-x_ctrl]
# A fresh subdirectory prevents stale output from masquerading as a new build.
set work_dir [file join $build_dir run-[clock seconds]-[pid]]
file mkdir [file join $work_dir sasebo_giii_ctrl]
foreach name $files {
    file copy [file join $source_dir $name] [file join $work_dir $name]
}
set old_dir [pwd]
cd [file join $work_dir sasebo_giii_ctrl]
set rc [catch {
    project open sasebo_giii_ctrl.xise
    puts "Building AIST Spartan-6 controller with its original project settings/UCF."
    puts "Part: [project get "Device"], [project get "Package"], [project get "Speed Grade"]"
    if {![string equal -nocase [project get "Device"] xc6slx45]} {
        error "Expected reference project for XC6SLX45; check your board/project."
    }
    process run "Generate Programming File"
    set status [process get "Generate Programming File" status]
    if {![string match "up_to_date*" $status]} {
        error "ISE bitstream process did not succeed: $status (reports: [pwd])"
    }
    set bits [glob -nocomplain *.bit]
    if {[llength $bits] != 1 || [file size [lindex $bits 0]] == 0} {
        error "Expected exactly one non-empty generated .bit in [pwd]"
    }
    set output [file join $build_dir sasebo_giii_ctrl.bit]
    file copy -force [lindex $bits 0] $output
    puts "RESULT: OK -- Spartan-6 bitstream: $output"
} msg]
catch {project close}
cd $old_dir
if {$rc} { error $msg }
