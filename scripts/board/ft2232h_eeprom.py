#!/usr/bin/env python3
"""Read / back up / fix the SAKURA-X FT2232H EEPROM on Linux (pyftdi; FT_PROG is Windows-only).

The sakura-x-shell link (and AIST's factory controller) needs Channel A in 245 FIFO
mode and Channel B in UART mode (third_party/sakura_x_shell/ft2232h_config/*.xml).
A board whose Channel A is a UART cannot talk to the Spartan-6 at all.

SAKURA-X boards carry a 93C46 EEPROM (128 bytes, chip byte 0x46). pyftdi assumes 256
bytes for an FT2232H, sees the 93C46 wrap around, mistakes it for FT_PROG "mirroring"
and fails on the first change with "IndexError: bytearray index out of range". This
script opens the EEPROM with its real size, so that does not happen.

    sudo ~/ftdi-venv/bin/python scripts/board/ft2232h_eeprom.py              # read only: summary + backup
    sudo ~/ftdi-venv/bin/python scripts/board/ft2232h_eeprom.py --write      # A = 245 FIFO, B = UART
    sudo ~/ftdi-venv/bin/python scripts/board/ft2232h_eeprom.py --write --rename   # + product "SAKURA-X Shell"
    sudo ~/ftdi-venv/bin/python scripts/board/ft2232h_eeprom.py --restore BACKUP.bin

Every run saves a raw backup first. --write shows the bytes it changes and asks before
writing (--yes skips the question), then reads the EEPROM back to verify. Unplug and
replug the board's USB afterwards: the FT2232H reads its EEPROM only at power-up.
Close the GUI and anything else using /dev/ttyUSB0 / ttyUSB1 first.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

SHELL_PRODUCT = "SAKURA-X Shell"
FT2232H = 0x0700


def ftdi_crc_ok(data: bytes) -> bool:
    """FTDI EEPROM checksum (last 16-bit word) of a raw image."""
    crc = 0xAAAA
    for i in range(0, len(data) - 2, 2):
        crc ^= data[i] | (data[i + 1] << 8)
        crc = ((crc << 1) | (crc >> 15)) & 0xFFFF
    return struct.unpack("<H", data[-2:])[0] == crc


def url_of(serial: str) -> str:
    return "ftdi://ftdi:2232h%s/1" % (":" + serial if serial else "")


def open_eeprom(url: str):
    """Open with the EEPROM's real size (93C46 = 128 bytes, read from the chip byte)."""
    from pyftdi.eeprom import FtdiEeprom
    probe = FtdiEeprom()
    probe.open(url)                     # default open: only the chip byte (0x18) is used from it
    chip = probe._config.get("chip")
    dev = probe.device_version
    probe.close()
    if dev != FT2232H:
        raise SystemExit("not an FT2232H (device version 0x%04x)" % dev)
    size = {0x46: 128, 0x56: 256, 0x66: 256}.get(chip)
    ee = FtdiEeprom()
    ee.open(url, size=size)
    return ee, chip, size or ee.size


def summary(ee, chip, size) -> dict:
    c = ee._config
    print("  EEPROM chip     : 0x%02x (%d bytes)" % (chip or 0, size))
    print("  manufacturer    : %r" % c.get("manufacturer"))
    print("  product         : %r" % c.get("product"))
    print("  serial          : %r" % c.get("serial"))
    print("  channel A       : %-5s driver %s   (must be FIFO)" % (c.get("channel_a_type"), c.get("channel_a_driver")))
    print("  channel B       : %-5s driver %s   (must be UART)" % (c.get("channel_b_type"), c.get("channel_b_driver")))
    return dict(c)                      # a copy: pyftdi clears its own dict on close()


def backup(ee, serial: str) -> Path:
    raw = bytes(ee._eeprom)
    path = Path("ft2232h_%s_%s.bin" % (serial or "board", time.strftime("%Y%m%d_%H%M%S"))).resolve()
    path.write_bytes(raw)
    print("  backup          : %s (%d bytes, checksum %s)" % (path, len(raw), "ok" if ftdi_crc_ok(raw) else "BAD"))
    return path


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    return input(question + " Type yes: ").strip().lower() == "yes"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serial", default="", help="board USB serial (default: the only FT2232H connected), e.g. FT9A4QV0")
    ap.add_argument("--write", action="store_true", help="set Channel A = 245 FIFO, Channel B = UART")
    ap.add_argument("--rename", action="store_true", help="with --write: also set the product name to 'SAKURA-X Shell'")
    ap.add_argument("--restore", type=Path, help="write a raw backup image back")
    ap.add_argument("--yes", action="store_true", help="do not ask before writing")
    a = ap.parse_args()
    if a.rename and not a.write:
        ap.error("--rename goes with --write")

    url = url_of(a.serial)
    ee, chip, size = open_eeprom(url)
    try:
        print("Current EEPROM (%s):" % url)
        cfg = summary(ee, chip, size)
        backup(ee, a.serial or cfg.get("serial") or "")

        if a.restore:
            data = a.restore.read_bytes()
            if len(data) != size or not ftdi_crc_ok(data):
                raise SystemExit("refusing: %s is %d bytes (EEPROM %d) or its checksum is bad" % (a.restore, len(data), size))
            if not confirm("Write %s back to the EEPROM?" % a.restore, a.yes):
                return 1
            ee._ftdi.overwrite_eeprom(data, dry_run=False)
            print("Restored. Unplug and replug the USB cable.")
            return 0

        if not a.write:
            ok = cfg.get("channel_a_type") == "FIFO" and cfg.get("channel_b_type") == "UART"
            print("\n=> " + ("Channel modes are correct for the shell (A FIFO, B UART)." if ok else
                            "Channel modes are WRONG for the shell. Fix them with --write."))
            return 0

        before = bytes(ee._eeprom)
        ee.set_property("channel_a_type", "FIFO")
        ee.set_property("channel_b_type", "UART")
        if a.rename:
            if not cfg.get("serial"):
                raise SystemExit("refusing --rename: the serial string could not be decoded, so rewriting the "
                                 "string area could lose it. Run --write without --rename.")
            ee.set_product_name(SHELL_PRODUCT)
        after = ee.data                       # regenerates strings / checksum, not yet written
        diff = [i for i in range(size) if before[i] != after[i]]
        if not diff:
            print("\nNothing to change: the EEPROM already has these settings.")
            return 0
        print("\nBytes that will change (offset: old -> new):")
        for i in diff[:24]:
            print("  0x%02x: %02x -> %02x" % (i, before[i], after[i]))
        if len(diff) > 24:
            print("  ... %d more (string area)" % (len(diff) - 24))
        print("New settings:")
        summary(ee, chip, size)
        if not confirm("\nWrite these settings to the EEPROM?", a.yes):
            print("Nothing written.")
            return 1
        ee.commit(dry_run=False)              # pyftdi reads the EEPROM back and compares
    finally:
        ee.close()

    ee2, chip2, size2 = open_eeprom(url)
    try:
        print("\nRead back after writing:")
        cfg2 = summary(ee2, chip2, size2)
    finally:
        ee2.close()
    if cfg2.get("channel_a_type") != "FIFO" or cfg2.get("channel_b_type") != "UART":
        print("=> VERIFY FAILED: the channel modes did not change.")
        return 2
    print("\n=> Done. Unplug the board's USB cable and plug it back in (the FT2232H reads its EEPROM only at "
          "power-up), then Refresh devices and Connect in the GUI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
