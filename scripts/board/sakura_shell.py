#!/usr/bin/env python3
"""Manual Linux shell commands and a persistent five-command session."""
from pathlib import Path
import argparse
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cpa_toolkit"))
import uart_target as T
from sakura_shell import PREFIX, ShellLink, ShellTarget, INPUT_READY

COMMANDS = ["RESET", "LOAD_DK", "LOAD_CT", "START", "READ_SS", "INFO", "CHECK", "SESSION"]


def execute(target, command, path=None):
    """Execute one application operation while retaining the USB connection."""
    link = target.link
    if command in ("LOAD_DK", "LOAD_CT"):
        if path is None:
            raise ValueError("LOAD_DK / LOAD_CT need a binary file")
        data = path.read_bytes()
        if command == "LOAD_DK":
            target.load_dk(data)
        else:
            target.load_ct(data)
        print("%s: %d bytes buffered; INPUT_READY=0x%x" %
              (command, len(data), link.read_words(INPUT_READY)[0]))
    elif command == "RESET":
        target.reset()
        print("RESET complete; reload DK and CT before START")
    elif command == "START":
        status = target.start()
        print("START complete: STATUS=0x%x CYCLES=%d ERROR=%d" %
              (status, link.rd("CYCLE_COUNT"), link.rd("ERROR_CODE")))
        if status != T.ST_DONE:
            return 2
    elif command == "READ_SS":
        ss = target.read_shared_secret()
        if path:
            path.write_bytes(ss)
        print(ss.hex())
    elif command == "INFO":
        print(target.info())
        print("INPUT_READY=0x%x (bit0 DK, bit1 CT)" % link.read_words(INPUT_READY)[0])
    elif command == "CHECK":
        for name, ok, detail in target.preflight():
            print("%s %s: %s" % ("PASS" if ok else "FAIL", name, detail))
            if not ok:
                return 2
        import mlkem768 as KEM
        ek, dk = KEM.keygen(bytes(range(32)), bytes(range(32, 64)))
        _, ct = KEM.encaps(ek, bytes(range(32)))
        for name, sample in (("valid", ct), ("invalid", bytes([ct[0] ^ 1]) + ct[1:])):
            expected = KEM.decaps(dk, sample)
            outcome = target.decaps(dk, sample, resend_dk=(name == "valid"))
            ok = outcome.ok and outcome.output == expected
            print("%s %s Decaps: %s" % ("PASS" if ok else "FAIL", name, outcome.describe()))
            if not ok:
                return 2
    else:
        raise ValueError("unknown application command: %s" % command)
    return 0


def main():
    """Open one board session and dispatch explicitly requested commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Channel A device or sakura-shell:<device>")
    parser.add_argument("--list", action="store_true", help="list ports without opening hardware")
    parser.add_argument("--hard", action="store_true", help="kept for old scripts: every session now starts with an RTS reset")
    parser.add_argument("--quiet-ms", type=float, default=20, help="START quiet tail after completion")
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    parser.add_argument("file", nargs="?", type=Path, help="binary DK/CT input, or READ_SS output")
    args = parser.parse_args()
    if args.list:
        for device, desc, hint, recommended in T.list_ports():
            print("%s  %s  %s%s" % (device, desc, hint, " [recommended]" if recommended else ""))
        return 0
    if not args.port or not args.command:
        parser.error("--port and a command are required (or use --list)")
    port = args.port if args.port.startswith(PREFIX) else PREFIX + args.port
    link = ShellLink(port)
    target = ShellTarget(link)
    try:
        link.set_quiet_window(args.quiet_ms / 1000)
        # RTS reset + which Spartan controller answers, before any Kintex access.
        print("Spartan controller: %s" % link.check_controller(
            lambda name, ok, detail: print("  %s %s: %s" % ("PASS" if ok else "FAIL", name, detail))))
        target.probe_run()
        if args.command != "SESSION":
            return execute(target, args.command, args.file)
        print("Persistent session: RESET, LOAD_DK file, LOAD_CT file, START, READ_SS [file], INFO; QUIT to close.")
        while True:
            try:
                line = input("sakura> ")
            except EOFError:
                return 0
            parts = shlex.split(line)
            if not parts:
                continue
            command = parts[0].upper()
            if command == "QUIT":
                return 0
            if command not in COMMANDS or command == "SESSION" or len(parts) > 2:
                print("Use RESET / LOAD_DK file / LOAD_CT file / START / READ_SS [file] / INFO / CHECK")
                continue
            code = execute(target, command, Path(parts[1]) if len(parts) == 2 else None)
            if code:
                return code
    finally:
        target.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (T.LinkError, T.ProtocolError, T.BitstreamError, ValueError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(2)
