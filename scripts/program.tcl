# program.tcl - Program a Xilinx FPGA with a bitstream
#
# Usage:
#   vivado -mode batch -source scripts/program.tcl -tclargs <bitstream_path>
#
# Example:
#   vivado -mode batch -source scripts/program.tcl -tclargs \
#       build/vivado/arty-a7-35t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit

if {$argc < 1} {
    puts "Usage: vivado -mode batch -source program.tcl -tclargs <bitstream.bit>"
    exit 1
}

set bitstream [lindex $argv 0]

if {![file exists $bitstream]} {
    puts "ERROR: Bitstream file not found: $bitstream"
    exit 1
}

puts "=== PQC Test Kit - FPGA Programming ==="
puts "Bitstream: $bitstream"

# Open hardware manager and connect to the first device found.
open_hw_manager
connect_hw_server -allow_non_jtag
open_hw_target

# Get the first device.
set device [lindex [get_hw_devices] 0]
current_hw_device $device
set_property PROGRAM.FILE $bitstream $device

puts "Programming device: [get_property NAME $device]..."
program_hw_devices $device

puts "Programming complete."

close_hw_target
disconnect_hw_server
close_hw_manager
