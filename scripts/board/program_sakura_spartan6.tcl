# Program only the SAKURA-X Spartan-6 via CN10. No ISE needed.
# Linux terminal: tclsh scripts/board/program_sakura_spartan6.tcl ?ctrl.bit? ?--dry-run?
# Vivado Hardware Manager does NOT support Spartan-6; this invokes openFPGALoader.
if {[llength [info commands get_hw_devices]]} {
    error "Spartan-6 is not supported by Vivado Hardware Manager. Close its cable connection and run this script with tclsh in a Linux terminal (openFPGALoader required)."
}
source [file join [file dirname [info script]] program_sakura_dual.tcl]
namespace eval sakura_spartan6 {}
proc sakura_spartan6::main {args} {
    set usage "Usage: tclsh program_sakura_spartan6.tcl ?controller.bit? ?--dry-run?"
    if {$args eq "--help"} { puts $usage; return }
    set dry 0
    if {[llength $args] && [lindex $args end] eq "--dry-run"} {
        set dry 1
        set args [lrange $args 0 end-1]
    }
    if {[llength $args] == 0} {
        set bit [file join $::sakura_dual::repo_root firmware sakura_x sasebo_giii_ctrl.bit]
    } elseif {[llength $args] == 1} {
        set bit [file normalize [lindex $args 0]]
    } else { error $usage }
    set part [sakura_dual::bit_part $bit]
    if {![regexp {^(xc)?6slx45(fgg|fg)484(-2)?$} $part]} {
        error "CN10 requires an XC6SLX45 FG(G)484 controller bitstream, got $part"
    }
    if {!$dry && [auto_execok openFPGALoader] eq ""} {
        error "Install openFPGALoader first (Debian/Ubuntu: sudo apt install openfpgaloader). ISE is not needed."
    }
    puts "Spartan-6 only: use one DLC10 on CN10; close other JTAG programs."
    sakura_dual::load_one openFPGALoader $bit xc6slx45 CN10 $dry
    if {$dry} {
        puts "RESULT: DRY RUN OK -- Spartan-6 only; no hardware accessed."
    } else {
        puts "RESULT: CN10 LOAD COMPLETED -- keep board power ON; next move the ribbon to CN5 and program Kintex-7 with Vivado."
    }
}
if {[file normalize [info script]] eq [file normalize $argv0]} {
    if {[catch {sakura_spartan6::main {*}$argv} msg]} {
        puts stderr "ERROR: $msg"
        exit 1
    }
}
