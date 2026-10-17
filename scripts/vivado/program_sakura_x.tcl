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
#   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs <bitstream.bit> [xvc-url]
#
#   xvc-url  optional Xilinx Virtual Cable server, host:port (a leading "tcp:"
#            is accepted), e.g. host.docker.internal:3721 for the openFPGALoader
#            XVC server used with Vivado in Docker. Without it, the cables
#            hw_server sees directly (e.g. a Platform Cable USB II on CN5) are used.
#
# Example:
#   vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs \
#       build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit

proc fail {msg} {
    puts "ERROR: $msg"
    error $msg
}

if {$argc < 1} {
    fail "usage: vivado -mode batch -source program_sakura_x.tcl -tclargs <bitstream.bit> \[xvc-url\]"
}
set bitstream [file normalize [lindex $argv 0]]
set xvc       [expr {$argc >= 2 ? [lindex $argv 1] : ""}]
regsub {^tcp:} $xvc {} xvc

if {![file exists $bitstream]} {
    fail "Bitstream file not found: $bitstream"
}

puts "=== PQC Test Kit - SAKURA-X programming (Kintex-7, JTAG CN5) ==="
puts "Bitstream: $bitstream"

open_hw_manager
connect_hw_server -allow_non_jtag

# Open each target in turn and keep the first one with a Kintex-7 on it.
set device ""
if {$xvc ne ""} {
    puts "XVC:       $xvc"
    open_hw_target -xvc_url $xvc
    set device [lindex [get_hw_devices -quiet xc7k*] 0]
} else {
    foreach target [get_hw_targets -quiet] {
        if {[catch {open_hw_target $target} msg]} {
            puts "  skipping $target: $msg"
            continue
        }
        set device [lindex [get_hw_devices -quiet xc7k*] 0]
        if {$device ne ""} {
            puts "Target:    $target"
            break
        }
        close_hw_target
    }
}

if {$device eq ""} {
    disconnect_hw_server
    close_hw_manager
    fail "no Kintex-7 found on any JTAG target: check the cable on CN5 (not CN10, that is the Spartan-6), board power (SW1) and, for XVC, the server URL"
}

current_hw_device $device
refresh_hw_device -update_hw_probes false $device
set_property PROGRAM.FILE $bitstream $device

puts "Programming device: [get_property NAME $device] ([get_property PART $device])..."
program_hw_devices $device
refresh_hw_device -update_hw_probes false $device

set done [get_property REGISTER.IR.BIT5_DONE $device]
puts "DONE pin: $done"
if {$done ne "1"} {
    puts "WARNING: DONE did not go high -- the bitstream may be for another part."
}
puts "Programming complete. LED10 should be lit (MMCM locked), LED4 blinking (heartbeat)."

close_hw_target
disconnect_hw_server
close_hw_manager
