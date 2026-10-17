# load_arty_z7_ps.tcl — bring up the Arty Z7-20 PS variant (run after every power-up).
#
# Programs the PL bitstream, initialises the PS (MIO, PLLs, clocks, UARTs,
# PS<->PL level shifters; no DDR -- the forwarder runs from on-chip memory)
# and starts the UART forwarder on CPU0. Same steps as the host-side
# scripts/board/boot_arty_z7_ps.py. Needs jumper JP4 on JTAG.
# After this, the host talks to the ML-KEM core over the board's on-board
# USB serial (the ...1 interface) at 115200, exactly like the Arty A7.
#
# Inputs (from scripts/vivado/build_arty_z7_ps.tcl and sw/arty_z7_uart_fwd):
#   build/vivado/arty-z7-ps_mlkem/out/arty_z7_ps_top.bit
#   build/vivado/arty-z7-ps_mlkem/out/ps7_init.tcl
#   build/arty_z7_uart_fwd/uart_fwd.elf
#
# Usage (xsdb ships with Vivado: $XILINX_VIVADO/bin/xsdb):
#   xsdb scripts/vivado/load_arty_z7_ps.tcl [xvc-url]
#
# JTAG path: by default, whatever cable the local hw_server sees directly --
# the Arty Z7's own USB-JTAG (its micro-USB port) or any Xilinx/Digilent cable on
# the machine running xsdb. No XVC server and no openFPGALoader are needed.
# If Vivado's Hardware Manager is already connected (directly or through a
# Virtual Cable), its hw_server on localhost:3121 is reused, so the board it
# shows is the one this script uses.
#
# Optional xvc-url: only when the cable is NOT local to hw_server, e.g. Vivado
# in a container without USB access, with an XVC server on the host
# (tcp:host.docker.internal:3721 for openFPGALoader --xvc). An XVC server must
# read TDO correctly for ARM-DAP accesses: openFPGALoader <= 1.1.1 does not
# ("Invalid DAP ACK value: 3", fixed upstream in commit 4700951).
# scripts/board/boot_arty_z7_ps.py is the host-side alternative without xsdb.
#
# Sourcing it from Vivado's Tcl console (Tools > Run Tcl Script) also works:
# Vivado has no ARM debug commands, so the script re-runs itself under xsdb
# (same default: the cable Vivado's hw_server sees) and prints xsdb's output.

if {![info exists argv]} { set argv {} }
if {[info commands dow] eq "" && "--from-vivado" ni $argv} {
    set xsdb [file join $::env(XILINX_VIVADO) bin xsdb]
    if {![file exists $xsdb] && ![file exists $xsdb.bat]} {
        return -code error "xsdb not found at $xsdb: run this script with xsdb from a Vivado/Vitis install"
    }
    puts "Not running under xsdb -- handing this script to $xsdb ..."
    set failed [catch {exec $xsdb [info script] --from-vivado 2>@1} log]
    puts $log
    if {$failed} {
        return -code error "load_arty_z7_ps.tcl failed under xsdb (see output above)"
    }
    return
}
set argv [lsearch -all -inline -not -exact $argv --from-vivado]

set repo [file normalize [file join [file dirname [info script]] .. ..]]
set out  "$repo/build/vivado/arty-z7-ps_mlkem/out"
set bit  "$out/arty_z7_ps_top.bit"
set init "$out/ps7_init.tcl"
set elf  "$repo/build/arty_z7_uart_fwd/uart_fwd.elf"

foreach {what path hint} [list \
    bitstream $bit  "run scripts/vivado/build_arty_z7_ps.tcl first" \
    ps7_init  $init "run scripts/vivado/build_arty_z7_ps.tcl first" \
    program   $elf  "run `make -C sw/arty_z7_uart_fwd` on the host first"] {
    if {![file exists $path]} {
        puts "ERROR: $what not found: $path\n       ($hint)"
        exit 1
    }
}

# Optional XVC server; without one (or with `none`) only local cables are used.
set xvc [expr {[llength $argv] >= 1 ? [lindex $argv 0] : "none"}]

# hw_server: reuse the one Vivado's Hardware Manager started, else start one.
if {[catch {connect -url tcp:localhost:3121}]} {
    connect
}
proc chain_visible {} { string match "*xc7z020*" [targets] }
# A freshly started hw_server, or a cable it has just opened, needs a few
# seconds to enumerate and scan its JTAG chain.
proc wait_chain {tries} {
    for {set i 0} {$i < $tries && ![chain_visible]} {incr i} { after 500 }
}
if {$xvc eq "none"} {
    wait_chain 20
} else {
    wait_chain 4
    if {![chain_visible]} {
        # Only (re)open the XVC cable when hw_server has no working one: reopening
        # it under Vivado's Hardware Manager drops Vivado's target, and the
        # openFPGALoader XVC server can wedge (all-zero scans) after such a drop.
        if {[catch {connect -xvc-url $xvc} msg]} {
            puts "note: XVC $xvc not (re)opened: $msg"
        }
        wait_chain 10
    }
}

proc select_target {pattern} {
    # The filter is an expression: the pattern must be a quoted string.
    if {[catch {targets -set -nocase -filter [format {name =~ "%s"} $pattern]} msg]} {
        puts "ERROR: no JTAG target matching '$pattern' ($msg). Targets visible:"
        set visible [targets]
        puts $visible
        if {[string match "*DAP (*" $visible]} {
            dap_locked_hint
        } else {
            puts "JTAG cables hw_server sees:"
            catch {puts [jtag targets]}
            puts "Check that the board is powered, its micro-USB (PROG/UART) port is connected, the"
            puts "cable driver is installed and no other program holds the cable (openFPGALoader,"
            puts "another hw_server). If hw_server cannot reach USB here (e.g. Vivado in a container),"
            puts "pass an XVC server: xsdb scripts/vivado/load_arty_z7_ps.tcl tcp:<host>:<port>"
        }
        exit 1
    }
}

# The ARM debug port answers on JTAG but cannot reach the PS: "Cannot open
# JTAG port" or "AHB AP transaction error". The BootROM keeps it locked after
# a failed SD/QSPI boot, and a timed-out access leaves it in a sticky error.
proc dap_locked_hint {} {
    puts "The ARM debug port (DAP) cannot reach the PS. Put jumper JP4 on JTAG, power the"
    puts "board off completely (USB and any external supply -- PROG/SRST are not enough),"
    puts "then run this again (or: uv run scripts/board/boot_arty_z7_ps.py on the host)."
}

# Same steps as scripts/board/boot_arty_z7_ps.py, which is known to work:
#   - no `rst -system`: it re-runs the BootROM, which locks the DAP unless JP4 is on JTAG;
#   - ps7_init without the DDR part: the forwarder runs from on-chip memory, and the
#     DDR calibration polls are where a slow or failing link used to hang;
#   - every mask_poll gives up after 2 s with a message (Xilinx's tries 100,000,000
#     reads -- over XVC that is "forever, silently").

puts "== checking the ARM debug port"
select_target "APU*"
if {[catch {mrd -value 0xF8000530} msg]} {        ;# PSS_IDCODE: answers on any reachable PS
    puts "ERROR: reading the PS failed: $msg"
    dap_locked_hint
    exit 1
}

puts "== programming the PL: $bit"
select_target "xc7z020*"       ;# the PL device itself, in case several cables are attached
fpga -file $bit

puts "== initialising the PS (ps7_init without DDR): $init"
select_target "APU*"
source $init
proc mask_poll {addr mask} {
    set deadline [expr {[clock milliseconds] + 2000}]
    while {([mrd -value $addr] & $mask) == 0} {
        if {[clock milliseconds] > $deadline} {
            error "mask_poll $addr & $mask did not complete within 2 s"
        }
    }
}
switch -- [ps_version] {             ;# the same choice ps7_init makes
    0x0     { set suffix 1_0 }
    0x1     { set suffix 2_0 }
    default { set suffix 3_0 }
}
if {[catch {
    foreach part {mio pll clock peripherals} {
        puts "   ps7_${part}_init_data_$suffix"
        ps7_${part}_init_data_$suffix
    }
    ps7_post_config            ;# enables the PS<->PL level shifters EMIO needs
} msg]} {
    puts "ERROR: ps7_init failed: $msg"
    dap_locked_hint
    exit 1
}

puts "== starting the UART forwarder on CPU0: $elf"
select_target "*A9*#0"
rst -processor
dow $elf
con

puts ""
puts "Done. Host side: connect to the board's on-board USB serial (...1 interface), 115200 baud."
