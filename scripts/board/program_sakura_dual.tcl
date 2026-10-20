# Sequential SRAM programming of both SAKURA-X FPGAs with one DLC10 cable.
# Run with plain tclsh, not in Vivado's Tcl console:
# tclsh scripts/board/program_sakura_dual.tcl [--dry-run]
# Optional explicit images: <ctrl.bit> <mlkem.bit> [--dry-run]
# Requires openFPGALoader on PATH; closes each connection before asking to move
# the ribbon. Never writes either Flash or the FTDI EEPROM.
namespace eval sakura_dual {
    variable repo_root [file normalize [file join [file dirname [info script]] .. ..]]
}

proc sakura_dual::u16 {data offset} {
    binary scan [string range $data $offset [expr {$offset + 1}]] H4 hex
    if {[string length $hex] != 4} { error "Truncated .bit header" }
    scan $hex %x n
    return $n
}

proc sakura_dual::bit_part {path} {
    if {![file isfile $path] || ![string equal -nocase [file extension $path] .bit]} {
        error "Expected an existing Xilinx .bit file (not .mcs): $path"
    }
    set f [open $path rb]
    set data [read $f 4096]
    close $f
    set pos [expr {2 + [u16 $data 0]}]
    if {[u16 $data $pos] != 1} { error "Invalid .bit header: $path" }
    incr pos 2
    foreach tag {a b c d} {
        if {[string index $data $pos] ne $tag} { error "Invalid .bit field $tag: $path" }
        incr pos
        set n [u16 $data $pos]
        incr pos 2
        if {$n < 1 || $pos + $n > [string length $data]} { error "Invalid .bit field length: $path" }
        set value [string trimright [string range $data $pos [expr {$pos + $n - 1}]] \x00]
        incr pos $n
        if {$tag eq "b"} { return [string tolower $value] }
    }
    error "No part in .bit header: $path"
}

proc sakura_dual::chain_index {output model} {
    set index ""
    set matches {}
    foreach line [split $output \n] {
        if {[regexp {^\s*index\s+([0-9]+):} $line -> i]} { set index $i }
        if {[regexp {^\s*model\s+(\S+)} $line -> found] &&
            [string equal -nocase $found $model] && $index ne ""} {
            lappend matches $index
        }
    }
    if {[llength $matches] != 1} {
        error "Expected exactly one $model on this header; found [llength $matches]. Check CN10/CN5 and board power."
    }
    return [lindex $matches 0]
}

proc sakura_dual::load_one {loader bit model header dry} {
    set base [list $loader -c xilinxPlatformCableUsb --freq 3000000]
    # XCF32P is a 16-bit-IR bypass device on CN10, not a programming target.
    # IDCODE/IR length: OpenOCD tcl/cpld/xilinx-xcf-p.cfg (Xilinx DS123).
    if {$header eq "CN10"} {
        lappend base --misc-device 0x05059093,16,XCF32P
    }
    puts "\n$header: $model <- $bit"
    if {$dry} {
        puts "DRY RUN: $base --detect"
        puts "DRY RUN: $base --index-chain <detected-index> --write-sram $bit"
        return
    }
    puts "Connect the DLC10 ribbon to $header. Keep CN15 power ON between both loads."
    puts -nonewline "Type $header to continue (anything else aborts): "
    flush stdout
    if {[gets stdin answer] < 0 || [string trim $answer] ne $header} {
        error "Aborted before programming $header."
    }
    if {[catch {exec {*}$base --detect 2>@1} output]} {
        puts $output
        error "JTAG detection failed at $header; close Vivado/iMPACT/XVC and retry."
    }
    puts $output
    set index [chain_index $output $model]
    # Use the detected FPGA index: CN10 also includes the XCF32P PROM.
    # Exec arguments are Tcl lists, so paths with spaces remain one argument.
    if {[catch {exec {*}$base --index-chain $index --write-sram $bit >@stdout 2>@stderr} msg]} {
        error "Programming $header failed: $msg"
    }
    puts "$header load completed."
}

proc sakura_dual::main {args} {
    variable repo_root
    set usage "Usage: tclsh program_sakura_dual.tcl ?<spartan-controller.bit> <kintex-mlkem.bit>? ?--dry-run?"
    if {$args eq "--help"} { puts $usage; return }
    set dry 0
    if {[llength $args] && [lindex $args end] eq "--dry-run"} {
        set dry 1
        set args [lrange $args 0 end-1]
    }
    if {[llength $args] == 0} {
        set ctrl [file join $repo_root firmware sakura_x sasebo_giii_ctrl.bit]
        set main [file join $repo_root firmware sakura_x sakura_x_top.bit]
    } elseif {[llength $args] == 2} {
        set ctrl [file normalize [lindex $args 0]]
        set main [file normalize [lindex $args 1]]
    } else { error $usage }
    # Preflight BOTH files before accessing hardware, including swapped files.
    set ctrl_part [bit_part $ctrl]
    set main_part [bit_part $main]
    if {![regexp {^(xc)?6slx45(fgg|fg)484(-2)?$} $ctrl_part]} {
        error "CN10 requires XC6SLX45 FG(G)484 controller bitstream, got $ctrl_part"
    }
    if {![regexp {^(xc)?7k(70|160|325)t(fbg|ffg)676(-[123])?$} $main_part -> prefix size]} {
        error "CN5 requires a SAKURA-X Kintex-7 FBG/FFG676 bitstream, got $main_part"
    }
    set loader openFPGALoader
    if {!$dry && [auto_execok $loader] eq ""} { error "Install openFPGALoader and add it to PATH." }
    puts "Spartan part: $ctrl_part; Kintex part: $main_part"
    puts "Use only one attached DLC10. Close other JTAG programs before proceeding."
    load_one $loader $ctrl xc6slx45 CN10 $dry
    load_one $loader $main xc7k${size}t CN5 $dry
    if {$dry} {
        puts "RESULT: DRY RUN OK -- files validated; no hardware accessed."
    } else {
        puts "RESULT: BOTH LOADS COMPLETED -- next run sakura_link_check.py; expected bridge ID 0x4D4B."
        puts "Both SRAM configurations are lost at power-off."
    }
}

if {[file normalize [info script]] eq [file normalize $argv0]} {
    if {[catch {sakura_dual::main {*}$argv} msg]} {
        puts stderr "ERROR: $msg"
        exit 1
    }
}
