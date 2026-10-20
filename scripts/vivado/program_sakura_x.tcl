# program_sakura_x.tcl - Load a bitstream into the SAKURA-X Kintex-7 over JTAG
#
# The Kintex-7 has its own JTAG header, CN5 (SASEBO-GIII spec Table 6; the
# Spartan-6 is on CN10, a separate chain). This is a volatile load: after a
# power cycle the Kintex-7 configures from its BPI flash again (SW3 mode
# pins, factory default Master BPI), so run this after every power-up.
#
# Unlike the generic program_bitstream.tcl (first device of the first
# target), this picks the JTAG target and device that actually hold a
# Kintex-7, so an Arty left plugged in is never programmed by mistake.
#
# Usage:
#   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs <bitstream.bit> [xvc-url] [jtag-hz]
#
#   Pass "-" as xvc-url to use local cables with a jtag-hz.
#   jtag-hz  optional JTAG clock, e.g. 3000000 if the default (Platform Cable
#            USB II: 12 MHz) gives scan errors over a long ribbon.
#
# Linux, Platform Cable USB II (red box, DLC10): the cable needs Vivado's
# cable driver (udev rules + firmware loader), installed once as root:
#   cd <Vivado>/data/xicom/cable_drivers/lin64/install_script/install_drivers
#   sudo ./install_drivers
# then unplug / replug the cable. The status LED turns from amber to green
# when the firmware is loaded; `lsusb` then shows 03fd:0008. Close any other
# Vivado / hw_server first.
#
#   xvc-url  optional Xilinx Virtual Cable server, host:port (a leading "tcp:"
#            is accepted), e.g. host.docker.internal:3721 for the openFPGALoader
#            XVC server used with Vivado in Docker. Without it, the cables
#            hw_server sees directly (e.g. a Platform Cable USB II on CN5) are used.
#
# Example:
#   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs \
#       build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
#
# From the Vivado GUI Tcl console (no -tclargs there):
#   source <repo>/scripts/vivado/program_sakura_x.tcl
# programs the bitstream of the open SAKURA-X project's impl_1 run, or else
# <repo>/build/vivado/sakura-x_mlkem/.../sakura_x_top.bit. To choose another
# file, cable or JTAG clock, set these before sourcing:
#   set sakura_bit /path/to/file.bit ; set sakura_xvc host:port ; set sakura_jtag_hz 3000000
# The Hardware Manager is left open afterwards. `source -notrace` hides the echo of every line.

proc fail {msg} {
    puts "ERROR: $msg"
    puts "RESULT: FAILED"
    flush stdout
    error $msg
}

# Arguments: -tclargs in batch mode; in the GUI (plain `source`, argc = 0) the
# optional variables sakura_bit / sakura_xvc / sakura_jtag_hz, then defaults.
set script_dir [file dirname [file normalize [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set nargs [expr {[info exists argc] ? $argc : 0}]
set args_ [expr {[info exists argv] ? $argv : {}}]

proc default_bitstream {repo_root} {
    # 1. the open project's own impl_1 run (the GUI right after build_sakura_x.tcl)
    if {![catch {current_project} prj] && $prj ne "" && ![catch {get_runs impl_1} run] && $run ne ""} {
        set f [file join [get_property DIRECTORY $run] sakura_x_top.bit]
        if {[file exists $f]} { return $f }
    }
    # 2. where build_sakura_x.tcl puts it
    return [file join $repo_root build vivado sakura-x_mlkem pqc_mlkem_sakura_x.runs impl_1 sakura_x_top.bit]
}

if {$nargs >= 1} {
    set bitstream [lindex $args_ 0]
    set xvc       [expr {$nargs >= 2 ? [lindex $args_ 1] : ""}]
    set jtag_hz   [expr {$nargs >= 3 ? [lindex $args_ 2] : ""}]
} else {
    set bitstream [expr {[info exists sakura_bit] ? $sakura_bit : [default_bitstream $repo_root]}]
    set xvc       [expr {[info exists sakura_xvc] ? $sakura_xvc : ""}]
    set jtag_hz   [expr {[info exists sakura_jtag_hz] ? $sakura_jtag_hz : ""}]
}
set bitstream [file normalize $bitstream]
if {$xvc eq "-"} { set xvc "" }
regsub {^tcp:} $xvc {} xvc

if {![file exists $bitstream]} {
    fail "bitstream not found: $bitstream -- build first (build_sakura_x.tcl), or pass it: -tclargs <file.bit> (batch) / set sakura_bit <file.bit> before sourcing (GUI)"
}

puts "=== PQC Test Kit - SAKURA-X programming (Kintex-7, JTAG CN5) ==="
puts "Bitstream: $bitstream"

# Reuse a Hardware Manager / hw_server the GUI already has open.
catch {open_hw_manager}
if {[llength [get_hw_servers -quiet]] == 0} {
    if {[catch {connect_hw_server -allow_non_jtag} msg]} {
        fail "cannot start/connect hw_server: $msg (another Vivado or hw_server running? close it and retry)"
    }
}
catch {close_hw_target}      ;# a target left open in the GUI would block opening another

proc set_freq {hz} {
    if {$hz eq ""} { return }
    if {[catch {set_property PARAM.FREQUENCY $hz [current_hw_target]} msg]} {
        puts "  note: could not set JTAG frequency $hz: $msg"
    } else {
        puts "  JTAG frequency: $hz Hz"
    }
}

# Open each target in turn and keep the first one with a Kintex-7 on it.
set device ""
if {$xvc ne ""} {
    puts "XVC:       $xvc"
    if {[catch {open_hw_target -xvc_url $xvc} msg]} {
        fail "cannot open XVC target $xvc: $msg"
    }
    set_freq $jtag_hz
    refresh_hw_target [current_hw_target]
    set device [lindex [get_hw_devices -quiet xc7k*] 0]
} else {
    set targets [get_hw_targets -quiet]
    puts "JTAG targets found: [llength $targets]"
    foreach target $targets { puts "  $target" }
    if {[llength $targets] == 0} {
        fail "hw_server sees no JTAG cable. Linux: install the cable driver (see the header of this script), replug the cable, check `lsusb` for 03fd:0008; the cable LED must be green with the board powered."
    }
    foreach target $targets {
        if {[catch {open_hw_target $target} msg]} {
            puts "  skipping $target: $msg"
            continue
        }
        set_freq $jtag_hz
        set devs [get_hw_devices -quiet]
        puts "  $target: devices = $devs"
        set device [lindex [get_hw_devices -quiet xc7k*] 0]
        if {$device ne ""} {
            puts "Target:    $target"
            break
        }
        close_hw_target
    }
}

if {$device eq ""} {
    fail "no Kintex-7 found on any JTAG target: the ribbon must be on CN5 (Kintex-7, next to the SMA connectors), not CN10 (Spartan-6); board power SW1 on; for an empty chain or scan errors try a lower jtag-hz (3rd -tclarg, or set sakura_jtag_hz, e.g. 3000000)"
}

current_hw_device $device
refresh_hw_device -update_hw_probes false $device
set_property PROGRAM.FILE $bitstream $device
set_property PROBES.FILE {} $device

puts "Programming device: [get_property NAME $device] ([get_property PART $device]) -- takes about 5 s, started [clock format [clock seconds] -format %H:%M:%S] ..."
flush stdout
if {[catch {program_hw_devices $device} msg]} {
    puts "program_hw_devices failed: $msg"
    puts "  - 'part mismatch' / IDCODE: the bitstream was built for another part (build_sakura_x.tcl 2nd argument)"
    puts "  - 'End of startup status: LOW' / DONE low: corrupt / wrong bitstream file, or JTAG signal integrity -- re-run with a lower jtag-hz"
    fail "programming failed"
}
refresh_hw_device -update_hw_probes false $device

set done [get_property REGISTER.IR.BIT5_DONE $device]
puts "DONE pin: $done"
if {$done ne "1"} {
    fail "DONE did not go high after programming -- the bitstream may be for another part"
}
puts "Programming complete. LED10 should be lit (MMCM locked), LED4 blinking (heartbeat)."
puts "RESULT: OK -- Kintex-7 configured with [file tail $bitstream] (DONE = 1)"
flush stdout
# Nothing is closed here: batch mode exits Vivado after the script anyway, and in the GUI
# the Hardware Manager stays open on the device.
