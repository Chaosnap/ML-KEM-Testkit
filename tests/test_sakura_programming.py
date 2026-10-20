"""Exercise the dual-FPGA Tcl workflow with a fake cable, never real hardware."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/board/program_sakura_dual.tcl"


def bitfile(path, part):
    data = b"\x00\x09" + b"\x0f\xf0" * 4 + b"\x00\x00\x01"
    for tag, value in ((b"a", b"fixture"), (b"b", part.encode()),
                       (b"c", b"2026/10/20"), (b"d", b"00:00:00")):
        data += tag + struct.pack(">H", len(value) + 1) + value + b"\0"
    path.write_bytes(data + b"e\0\0\0\4\xff\xff\xff\xff")


@unittest.skipUnless(shutil.which("tclsh"), "tclsh required")
class DualProgrammingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sakura test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ctrl = self.root / "control file.bit"
        self.main = self.root / "main file.bit"
        bitfile(self.ctrl, "6slx45fgg484")
        bitfile(self.main, "7k160tfbg676")
        self.log = self.root / "calls.log"
        fake = self.root / "openFPGALoader"
        fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
if [ "${5}" = "--detect" ] || [ "${7}" = "--detect" ]; then
  if [ -f "$FAKE_LOG.loaded" ]; then
    printf 'index 0:\\n  model  xc7k160t\\n'
  else
    printf 'index 0:\\n  model  xcf32p\\nindex 1:\\n  model  %s\\n' "${FAKE_MODEL:-xc6slx45}"
  fi
else
  [ "$FAKE_FAIL" = 1 ] && exit 7
  touch "$FAKE_LOG.loaded"
fi
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        FAKE_LOG=str(self.log))

    def run_script(self, args=None, answer="CN10\nCN5\n"):
        return subprocess.run(["tclsh", str(SCRIPT), *(args or [str(self.ctrl), str(self.main)])],
                              input=answer, text=True, capture_output=True, env=self.env)

    def test_both_loads_in_order_and_prom_index_skipped(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 4)
        self.assertIn("--index-chain 1 --write-sram " + str(self.ctrl), calls[1])
        self.assertIn("--index-chain 0 --write-sram " + str(self.main), calls[3])
        self.assertNotIn("--write-flash", self.log.read_text())

    def test_swapped_bitstreams_never_access_cable(self):
        p = self.run_script([str(self.main), str(self.ctrl)])
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_missing_second_file_never_accesses_cable(self):
        self.main.unlink()
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_wrong_header_never_programs(self):
        self.env["FAKE_MODEL"] = "xc7k160t"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_load_failure_stops_before_kintex(self):
        self.env["FAKE_FAIL"] = "1"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_eof_does_not_skip_cable_prompt(self):
        self.assertNotEqual(self.run_script(answer="").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_dry_run_never_accesses_cable(self):
        p = self.run_script([str(self.ctrl), str(self.main), "--dry-run"], answer="")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DRY RUN OK", p.stdout)
        self.assertFalse(self.log.exists())

    def test_spartan_only_does_not_program_kintex(self):
        single = ROOT / "scripts/board/program_sakura_spartan6.tcl"
        p = subprocess.run(["tclsh", str(single), str(self.ctrl)],
                           input="CN10\n", text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("--index-chain 1 --write-sram", calls[1])
        self.assertNotIn(str(self.main), self.log.read_text())

    def test_spartan_only_rejects_kintex_image_before_cable_access(self):
        single = ROOT / "scripts/board/program_sakura_spartan6.tcl"
        p = subprocess.run(["tclsh", str(single), str(self.main)],
                           input="CN10\n", text=True, capture_output=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_bundle_defaults_work_outside_repository(self):
        # Model a clone without build/ or the author's external Downloads tree.
        clone = self.root / "clone with spaces"
        script = clone / "scripts/board/program_sakura_dual.tcl"
        script.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, script)
        images = clone / "firmware/sakura_x"
        images.mkdir(parents=True)
        shutil.copyfile(self.ctrl, images / "sasebo_giii_ctrl.bit")
        shutil.copyfile(self.main, images / "sakura_x_top.bit")
        p = subprocess.run(["tclsh", str(script), "--dry-run"], cwd=self.root,
                           text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DRY RUN OK", p.stdout)
        self.assertFalse(self.log.exists())
        p = subprocess.run(["tclsh", str(script)], cwd=self.root,
                           input="CN10\nCN5\n", text=True,
                           capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 4)

    def test_ambiguous_chain_is_rejected(self):
        text = f'''source {{{SCRIPT}}}
if {{![catch {{sakura_dual::chain_index "index 0:\\n model xc6slx45\\nindex 1:\\n model xc6slx45" xc6slx45}}]}} {{exit 1}}
'''
        p = subprocess.run(["tclsh"], input=text, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
