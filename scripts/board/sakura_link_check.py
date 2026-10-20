#!/usr/bin/env python3
"""Where does the SAKURA-X board-USB link break? (FT2232H -> Spartan-6 -> Kintex-7)

The GUI's ``sakura:`` port talks to the Spartan-6's factory control design through
the FT2232H's channel A (245-FIFO mode). The factory controller remains in reset
until its clock locks, external reset is released, and Kintex-7 DONE is high.
Once running, it returns two bytes per valid register read; an invalid command
can latch its parser in TRAP until controller reset. No reply alone does not
identify a faulty FPGA, EEPROM, or flash. This script checks, in order:

1. which FTDI ports belong to the board, with the USB strings the FT2232H's EEPROM
   reports (AIST's settings say "USB <-> Serial Converter"; "Dual RS232-HS" can
   indicate default settings, but strings alone do not establish the mode);
2. a raw read of the bridge's ID register (bytes 00 FF FC) on channel A, and on
   channel B in case the channels are swapped on this board;
3. with --eeprom (needs pyftdi; detaches the ttyUSB driver, replug afterwards): the
   EEPROM's channel A mode, which must be FIFO.

    uv run python scripts/board/sakura_link_check.py
    uv run python scripts/board/sakura_link_check.py --port /dev/ttyUSB0
    uv run --with pyftdi python scripts/board/sakura_link_check.py --eeprom
"""

from __future__ import annotations

import argparse
import sys
import time

ID_READ = bytes([0x00, 0xFF, 0xFC])      # read address 0xFFFC (sakura_lbus_bridge ID = 0x4D4B)
BRIDGE_ID = b"\x4d\x4b"


def board_ports():
    from serial.tools import list_ports
    out = []
    for p in list_ports.comports():
        if p.vid == 0x0403 and p.pid in (0x6010, 0x6014, 0x6011, 0x6001):
            desc = " / ".join(str(x) for x in (p.manufacturer, p.product, p.description) if x)
            iface = p.location.rsplit(".", 1)[-1] if p.location and ":1." in p.location else "?"
            out.append((p.device, iface, desc, p.serial_number or "", p.location or ""))
    return out


def probe(device: str, reads: int = 3, wait: float = 1.5) -> bytes:
    import serial
    with serial.Serial(device, 115200, timeout=0.1) as s:
        try:
            s.set_low_latency_mode(True)
        except Exception:
            pass
        time.sleep(0.05)
        s.reset_input_buffer()
        s.write(ID_READ * reads)
        got, t0 = b"", time.time()
        while time.time() - t0 < wait and len(got) < 2 * reads:
            got += s.read(2 * reads - len(got))
        return got


def verdict(got: bytes, reads: int) -> str:
    if not got:
        return "NO REPLY"
    if len(got) < 2 * reads:
        return "PARTIAL (%d of %d bytes)" % (len(got), 2 * reads)
    words = [got[i:i + 2] for i in range(0, len(got), 2)]
    if all(w == BRIDGE_ID for w in words):
        return "OK: Spartan-6 answers and the Kintex-7 bridge returns its ID 0x4D4B"
    return ("Received data %s, not 4D 4B: check the selected port, stale data, Kintex-7 "
            "bitstream, clock/reset and local-bus signals"
            % " ".join(w.hex() for w in words))


def eeprom_dump(device_index: int) -> None:
    try:
        from pyftdi.eeprom import FtdiEeprom
    except ImportError:
        print("  pyftdi is missing: run with `uv run --with pyftdi python ...`")
        return
    url = "ftdi://ftdi:2232h/%d" % (device_index + 1)
    ee = FtdiEeprom()
    try:
        ee.open(url)
    except Exception as e:  # noqa: BLE001
        print("  cannot open %s: %s" % (url, e))
        print("  (needs a udev rule for 0403:6010 or sudo; pyftdi detaches the ttyUSB driver)")
        return
    import io
    buf = io.StringIO()
    ee.dump_config(buf)
    lines = buf.getvalue().splitlines()
    for ln in lines:
        if any(k in ln.lower() for k in ("channel", "product", "manufacturer", "serial", "type")):
            print("   ", ln)
    a = [ln for ln in lines if "channel_a_type" in ln.lower()]
    if a:
        ok = "fifo" in a[0].lower()
        print("  channel A mode: %s" % ("FIFO -- correct" if ok else a[0].split(":", 1)[-1].strip() +
              " -- WRONG: must be 245 FIFO (reprogram the EEPROM with AIST's sasebo_giii.xml, FT_Prog)"))
    ee.close()
    print("  replug the board's USB cable now so the ttyUSB ports come back")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", help="probe only this port (default: every FT2232H port found)")
    ap.add_argument("--eeprom", action="store_true", help="also read the FT2232H EEPROM (pyftdi)")
    a = ap.parse_args()

    print("1. FTDI ports")
    ports = board_ports()
    if not ports:
        print("   none: the board's USB (CN15) is not seen. Cable, SW1 = USB, `lsusb` should show 0403:6010.")
        return 1
    for dev, iface, desc, sn, loc in ports:
        print("   %-14s interface %s  %s  serial %s  (%s)" % (dev, iface, desc, sn, loc))
    if any("Dual RS232" in d for _, _, d, _, _ in ports):
        print("   ! 'Dual RS232-HS' suggests default USB strings; verify channel A mode before changing EEPROM.")

    print("\n2. ID read (00 FF FC) x 3")
    targets = [a.port] if a.port else [d for d, *_ in ports]
    answered = False
    for dev in targets:
        try:
            got = probe(dev)
        except Exception as e:  # noqa: BLE001
            print("   %-14s cannot open: %s" % (dev, e))
            if "ermission" in str(e):
                print("                  add your user to the dialout group (sudo usermod -aG dialout $USER, log out/in)")
            if "busy" in str(e).lower() or "Errno 16" in str(e):
                print("                  another program holds it: close the GUI / other terminals first")
            continue
        v = verdict(got, 3)
        answered |= bool(got)
        print("   %-14s %-28s %s" % (dev, got.hex(" ") or "-", v))

    if not answered:
        print("\n   No reply: this does not prove failed programming or overwritten flash.")
        print("   - Keep board power on: both current .bit downloads are volatile.")
        print("   - Factory Spartan reset release requires clock lock, external reset and Kintex DONE.")
        print("     Check LED13 (Kintex DONE), LED20 (external reset released), and LED11 (heartbeat must blink).")
        print("   - Close GUI/other serial clients. Invalid raw UART commands can latch CTRL_LBUS in TRAP;")
        print("     reset/reload the Spartan controller, then run this probe BEFORE reconnecting the GUI.")
        print("   - Check FT2232H channel A is 245 FIFO; USB product strings alone do not prove the mode.")
    if a.eeprom:
        print("\n3. FT2232H EEPROM")
        eeprom_dump(0)
    return 0 if answered else 2


if __name__ == "__main__":
    sys.exit(main())
