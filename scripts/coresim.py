"""Run batches of ML-KEM operations on the RTL core in Verilator.

Shared by scripts/sim_acvp.py and scripts/profile_ucode.py. Builds
testbench/core_sim (tb_core.sv + hdl/core) on demand and talks to it
through a job file / result file; see tb_core.sv for the formats.
"""

import collections
import os
import re
import subprocess
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SIM_DIR = os.path.join(ROOT, "testbench", "core_sim")
BINARY = os.path.join(SIM_DIR, "obj_dir", "Vtb_core")

OPS = {"keygen": 0, "encaps": 1, "decaps": 2}
SIZES = {768: (1184, 2400, 1088)}    # level -> (ek, dk, ct)

Job = collections.namedtuple("Job", "op level data")
Result = collections.namedtuple("Result", "status err cycles out")

_JOB_RE = re.compile(r"JOB (\d+) status=(\d+) err=(\d+) cycles=(\d+) len=(\d+) out=([0-9a-f]*)")


def build(quiet=True):
    """(Re)build the Verilator model if any RTL source changed."""
    r = subprocess.run(["make", "-C", SIM_DIR], capture_output=quiet, text=True)
    if r.returncode != 0:
        raise SystemExit("Verilator build failed:\n%s%s" % (r.stdout or "", r.stderr or ""))


def run(jobs, profile=False, workdir=None):
    """Run jobs; returns [Result] and, with profile=True, the raw profile rows
    [(job, pc, op, exec_cycles, overhead_cycles)]."""
    build()
    tmp = workdir or tempfile.mkdtemp(prefix="coresim_")
    jf, of, pf = (os.path.join(tmp, n) for n in ("jobs.txt", "out.txt", "prof.txt"))
    with open(jf, "w") as f:
        f.write("%d\n" % len(jobs))
        for j in jobs:
            f.write("%d %d %d\n" % (j.op, j.level, len(j.data)))
            f.write(" ".join("%02x" % b for b in j.data) + "\n")
    cmd = [BINARY, "+jobs=" + jf, "+out=" + of]
    if profile:
        cmd.append("+prof=" + pf)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or "TB_CORE DONE" not in r.stdout:
        raise SystemExit("simulation failed:\n%s%s" % (r.stdout, r.stderr))
    results = []
    with open(of) as f:
        for line in f:
            m = _JOB_RE.match(line)
            if m:
                results.append(Result(int(m.group(2)), int(m.group(3)), int(m.group(4)),
                                      bytes.fromhex(m.group(6))))
    if len(results) != len(jobs):
        raise SystemExit("got %d results for %d jobs" % (len(results), len(jobs)))
    if not profile:
        return results
    rows = []
    with open(pf) as f:
        for line in f:
            p = line.split()
            rows.append(tuple(int(x) for x in p[1:]))
    return results, rows
