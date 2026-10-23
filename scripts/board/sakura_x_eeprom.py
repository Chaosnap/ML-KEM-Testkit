#!/usr/bin/env python3
"""One-click FT2232H EEPROM tool for the SAKURA-X board (Linux; FT_PROG is Windows-only).

The SAKURA-X link needs Channel A in 245 FIFO mode and Channel B in UART mode
(third_party/sakura_x_shell/ft2232h_config/sakura-x-shell.xml). With Channel A in
UART mode the PC cannot talk to the Spartan-6 at all.

    bash scripts/board/sakura_x_eeprom.sh            # fix: back up, set A = FIFO, B = UART, verify
    bash scripts/board/sakura_x_eeprom.sh restore    # write the original backup back, verify
    bash scripts/board/sakura_x_eeprom.sh check      # read only

The .sh wrapper installs pyftdi into ~/ftdi-venv once and runs this script as root.
Backups go to data/eeprom/: <serial>_original.bin is the image from before the first
change and is never replaced; every run also adds <serial>_<time>.bin. Digilent
FT2232H devices (Arty boards) are never touched.

SAKURA-X boards carry a 93C46 EEPROM (128 bytes, chip byte 0x46). pyftdi assumes 256
bytes for an FT2232H, sees the 93C46 wrap around, mistakes it for FT_PROG "mirroring"
and fails on the first change with "IndexError: bytearray index out of range". This
script opens the EEPROM with its real size, so that does not happen.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "data" / "eeprom"
SHELL_PRODUCT = "SAKURA-X Shell"
FT2232H = 0x0700

NEXT_STEPS = ("Next:\n"
              "  1. Disconnect the board USB cable and connect it again (the FT2232H reads the\n"
              "     EEPROM only at power-up).\n"
              "  2. If the board gets power from USB (SW1 = USB), load both FPGAs again.\n"
              "  3. In the GUI: Refresh, then Connect.")


class Stop(SystemExit):
    """A clear message for the operator, no traceback."""

    def __init__(self, msg: str):
        super().__init__("\nSTOP: " + msg)


def ftdi_crc_ok(data: bytes) -> bool:
    """FTDI EEPROM checksum (last 16-bit word) of a raw image."""
    crc = 0xAAAA
    for i in range(0, len(data) - 2, 2):
        crc ^= data[i] | (data[i + 1] << 8)
        crc = ((crc << 1) | (crc >> 15)) & 0xFFFF
    return struct.unpack("<H", data[-2:])[0] == crc


def url_of(serial: str) -> str:
    return "ftdi://ftdi:2232h%s/1" % (":" + serial if serial else "")


def find_boards() -> list:
    """USB descriptors (.sn, .description) of all connected FT2232H devices."""
    from pyftdi.usbtools import UsbTools
    return [d for d, _n in UsbTools.find_all([(0x0403, 0x6010)], nocache=True)]


def pick_board(serial: str, yes: bool) -> str:
    """The serial of the one SAKURA-X FT2232H; Digilent (Arty) devices are skipped."""
    found = find_boards()
    boards = [d for d in found if "digilent" not in (d.description or "").lower()]
    if serial:
        if not any(d.sn == serial for d in boards):
            raise Stop("no SAKURA-X FT2232H with serial %s. Connected: %s"
                       % (serial, ", ".join(str(d.sn) for d in boards) or "none"))
        return serial
    if not boards:
        raise Stop("no SAKURA-X FT2232H found%s. Connect the SAKURA-X USB cable and turn the board on."
                   % (" (only Digilent / Arty devices are connected)" if found else ""))
    if len(boards) == 1:
        return boards[0].sn or ""
    print("More than one FT2232H is connected:")
    for i, d in enumerate(boards, 1):
        print("  %d) serial %s  %r" % (i, d.sn, d.description))
    if yes or not sys.stdin.isatty():
        raise Stop("select one board with --serial.")
    choice = input("Type the number of the SAKURA-X board: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(boards):
        raise Stop("no board selected.")
    return boards[int(choice) - 1].sn or ""


def open_eeprom(url: str):
    """Open with the EEPROM's real size (93C46 = 128 bytes, read from the chip byte)."""
    from pyftdi.eeprom import FtdiEeprom
    probe = FtdiEeprom()
    probe.open(url)                     # default open: only the chip byte (0x18) is used from it
    chip = probe._config.get("chip")
    dev = probe.device_version
    probe.close()
    if dev != FT2232H:
        raise Stop("not an FT2232H (device version 0x%04x)" % dev)
    size = {0x46: 128, 0x56: 256, 0x66: 256}.get(chip)
    ee = FtdiEeprom()
    ee.open(url, size=size)
    return ee, chip, size or ee.size


def summary(ee, chip, size) -> dict:
    c = ee._config
    print("  EEPROM chip : 0x%02x (%d bytes)" % (chip or 0, size))
    print("  product     : %r" % c.get("product"))
    print("  serial      : %r" % c.get("serial"))
    print("  channel A   : %-5s (must be FIFO)" % c.get("channel_a_type"))
    print("  channel B   : %-5s (must be UART)" % c.get("channel_b_type"))
    return dict(c)                      # a copy: pyftdi clears its own dict on close()


def modes_ok(cfg: dict) -> bool:
    return cfg.get("channel_a_type") == "FIFO" and cfg.get("channel_b_type") == "UART"


def give_to_user(path: Path) -> None:
    """Under sudo, leave backups owned by the operator, not by root."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid and hasattr(os, "geteuid") and os.geteuid() == 0:
        for p in (path.parent, path):
            try:
                os.chown(p, int(uid), int(gid))
            except OSError:
                pass


def older_backups(tag: str, size: int) -> list[Path]:
    """Valid backups by the earlier version of this script (ft2232h_<serial>_<time>.bin in
    the directory it ran from), oldest first."""
    dirs = {Path.cwd(), ROOT, ROOT / "scripts" / "board", Path.home()}
    if os.environ.get("SUDO_USER"):
        dirs.add(Path("~" + os.environ["SUDO_USER"]).expanduser())
    found = set()
    for d in dirs:
        for p in d.glob("ft2232h_%s_*.bin" % tag):
            data = p.read_bytes()
            if len(data) == size and ftdi_crc_ok(data):
                found.add(p.resolve())
    return sorted(found, key=lambda p: p.name)


def save_backups(raw: bytes, tag: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = BACKUP_DIR / ("%s_%s.bin" % (tag, time.strftime("%Y%m%d_%H%M%S")))
    now.write_bytes(raw)
    give_to_user(now)
    original = BACKUP_DIR / ("%s_original.bin" % tag)
    if not original.exists():
        older = older_backups(tag, len(raw))
        original.write_bytes(older[0].read_bytes() if older else raw)
        give_to_user(original)
        print("  original    : %s%s" % (original, " (copied from %s)" % older[0] if older else ""))
    print("  backup      : %s" % now)
    return original


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_check(url, tag, a) -> int:
    ee, chip, size = open_eeprom(url)
    try:
        print("EEPROM of %s:" % url)
        cfg = summary(ee, chip, size)
        save_backups(bytes(ee._eeprom), tag or cfg.get("serial") or "board")
    finally:
        ee.close()
    if modes_ok(cfg):
        print("\nOK: the channel modes are correct (A = FIFO, B = UART).")
    else:
        print("\nWRONG: the channel modes are not A = FIFO, B = UART. Run the tool without 'check' to fix them.")
    return 0


def cmd_fix(url, tag, a) -> int:
    ee, chip, size = open_eeprom(url)
    try:
        print("EEPROM of %s:" % url)
        cfg = summary(ee, chip, size)
        save_backups(bytes(ee._eeprom), tag or cfg.get("serial") or "board")

        before = bytes(ee._eeprom)
        changes = []
        for key, want in (("channel_a_type", "FIFO"), ("channel_b_type", "UART")):
            if cfg.get(key) != want:
                changes.append("%s: %s -> %s" % ("Channel A" if key.endswith("a_type") else "Channel B",
                                                  cfg.get(key), want))
            ee.set_property(key, want)
        if not a.keep_name and cfg.get("product") != SHELL_PRODUCT:
            if cfg.get("serial"):
                ee.set_product_name(SHELL_PRODUCT)
                changes.append("Product name: %r -> %r (the GUI then finds the board itself)"
                               % (cfg.get("product"), SHELL_PRODUCT))
            else:
                print("\nNote: the serial string cannot be read, so the product name stays as it is "
                      "(a new name could lose the serial).")
        after = ee.data                       # regenerates strings / checksum, not yet written
        if before == after:
            print("\nOK: nothing to change. The EEPROM is already correct.")
            return 0
        print("\nChanges:")
        for c in changes:
            print("  - " + c)
        if not confirm("\nWrite these changes to the EEPROM?", a.yes):
            print("Nothing written.")
            return 1
        ee.commit(dry_run=False)              # pyftdi reads the EEPROM back and compares
    finally:
        ee.close()

    ee2, chip2, size2 = open_eeprom(url)
    try:
        print("\nEEPROM after the write:")
        cfg2 = summary(ee2, chip2, size2)
    finally:
        ee2.close()
    if not modes_ok(cfg2):
        print("\nFAILED: the channel modes did not change. Restore with: sakura_x_eeprom.sh restore")
        return 2
    print("\nDONE: the EEPROM is correct.\n" + NEXT_STEPS)
    return 0


def cmd_restore(url, tag, a) -> int:
    ee, chip, size = open_eeprom(url)
    try:
        print("EEPROM of %s:" % url)
        cfg = summary(ee, chip, size)
        serial = tag or cfg.get("serial") or "board"
        current = bytes(ee._eeprom)
        original = save_backups(current, serial)
        path = a.file or original
        data = path.read_bytes()
        if len(data) != size or not ftdi_crc_ok(data):
            raise Stop("%s is not a valid image for this EEPROM (%d bytes, %d expected, or bad checksum)."
                       % (path, len(data), size))
        if cfg.get("serial") and cfg["serial"].encode("utf-16le") not in data and not a.force:
            raise Stop("%s does not contain the serial %s of this board. It is for another board. "
                       "Use --force only if you are sure." % (path, cfg["serial"]))
        if data == current:
            print("\nOK: nothing to change. The EEPROM is already the same as %s." % path.name)
            return 0
        print("\nRestore from: %s (%d bytes change)" % (path, sum(x != y for x, y in zip(data, current))))
        if not confirm("Write this backup to the EEPROM?", a.yes):
            print("Nothing written.")
            return 1
        ee._ftdi.overwrite_eeprom(data, dry_run=False)
    finally:
        ee.close()

    ee2, chip2, size2 = open_eeprom(url)
    try:
        print("\nEEPROM after the restore:")
        summary(ee2, chip2, size2)
        same = bytes(ee2._eeprom) == data
    finally:
        ee2.close()
    if not same:
        print("\nFAILED: the EEPROM is not the same as the backup.")
        return 2
    print("\nDONE: the backup is restored and verified.\n" + NEXT_STEPS)
    return 0


def usb_error(e) -> str | None:
    """An operator message for the usual pyusb errors, or None."""
    errno = getattr(e, "errno", None) or getattr(getattr(e, "__cause__", None), "errno", None)
    text = str(e).lower()
    if errno == 13 or "access denied" in text or "permission" in text:
        return "no permission for the USB device. Run: bash scripts/board/sakura_x_eeprom.sh (it uses sudo)."
    if errno == 16 or "busy" in text:
        return "the USB device is in use. Close the GUI and all programs that use the board, then try again."
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="fix", choices=("fix", "restore", "check"),
                    help="fix (default): set A = FIFO, B = UART; restore: write the original backup back; "
                         "check: read only")
    ap.add_argument("--serial", default="", help="board USB serial, if more than one FT2232H is connected")
    ap.add_argument("--file", type=Path, help="restore: this backup instead of <serial>_original.bin")
    ap.add_argument("--keep-name", action="store_true", help="fix: do not change the product name")
    ap.add_argument("--force", action="store_true", help="restore: allow a backup of another board")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask before writing")
    a = ap.parse_args()
    try:
        import pyftdi  # noqa: F401
    except ImportError:
        raise Stop("pyftdi is missing. Run: bash scripts/board/sakura_x_eeprom.sh (it installs pyftdi).")
    try:
        tag = pick_board(a.serial, a.yes)
        return {"fix": cmd_fix, "check": cmd_check, "restore": cmd_restore}[a.command](url_of(tag), tag, a)
    except Stop:
        raise
    except Exception as e:  # pyusb / pyftdi errors
        msg = usb_error(e)
        if msg is None:
            raise
        if "permission" in msg and sys.platform.startswith("linux") and os.geteuid() != 0 and sys.stdin.isatty():
            print("Root access is necessary for the USB device. Starting again with sudo ...")
            os.execvp("sudo", ["sudo", sys.executable, *sys.argv])
        raise Stop(msg)


if __name__ == "__main__":
    sys.exit(main())
