"""Fixed-versus-random input sets for a measurement campaign.

A TVLA campaign needs two classes of inputs in random order: one *fixed*
input repeated unchanged, and *random* inputs drawn afresh for every trace.
Interleaving them randomly is what makes the test robust against slow drift
in the setup (temperature, supply, probe position): any such drift affects
both classes equally and therefore cancels in the difference of means.

The vectors themselves come from the Go implementation in ``pkg/sca``
(``pqc-testkit sca gen``), which uses the same ML-KEM-768 reference code as
the functional tests, so the host never needs its own implementation of the
scheme. This module runs that command and reads the CSV it writes::

    index,class,input_hex

with class 0 = fixed, 1 = random. For ML-KEM-768 Decaps ``input_hex`` is
``dk || c`` (2400 + 1088 bytes), which is exactly the byte string the core
expects in its data buffer.

Note on NIST vectors: the ACVP files under ``acvp/`` are *functional*
known-answer tests. They are a fixed, small set with no fixed/random
structure, so they validate correctness (``scripts/acvp_test.py``) but are
not an input set for leakage assessment. Use this module for campaigns and
ACVP for "does the core still compute the right answer".
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from dataclasses import dataclass

from . import protocol as P

#: Repository root, two levels above this file (hostlab/tracelab/).
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

#: The Go CLI; built on demand by :func:`ensure_cli`.
CLI = os.path.join(ROOT, "build", "pqc-testkit")


@dataclass
class Vector:
    """One campaign input."""

    index: int
    cls: int  # 0 = fixed, 1 = random
    data: bytes

    @property
    def class_name(self) -> str:
        return "fixed" if self.cls == 0 else "random"


@dataclass
class VectorSet:
    """A shuffled fixed/random input set, with the parameters that made it."""

    vectors: list
    op: int
    op_name: str
    seed: str = ""
    invalid_random: bool = False
    source: str = ""

    def __len__(self) -> int:
        return len(self.vectors)

    def __iter__(self):
        return iter(self.vectors)

    def __getitem__(self, i):
        return self.vectors[i]

    def counts(self) -> tuple:
        fixed = sum(1 for v in self.vectors if v.cls == 0)
        return fixed, len(self.vectors) - fixed

    def meta(self) -> dict:
        fixed, random = self.counts()
        return {
            "op": self.op_name,
            "traces": len(self.vectors),
            "fixed": fixed,
            "random": random,
            "input_bytes": len(self.vectors[0].data) if self.vectors else 0,
            "seed": self.seed,
            "invalid_random": self.invalid_random,
            "source": self.source,
        }


def ensure_cli(quiet: bool = False) -> str:
    """Build ``build/pqc-testkit`` if it is missing, and return its path."""
    if os.path.exists(CLI):
        return CLI
    if not quiet:
        print("building the Go CLI (build/pqc-testkit) ...", flush=True)
    r = subprocess.run(
        ["go", "build", "-o", CLI, "./cmd/pqc-testkit"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise SystemExit("go build failed:\n" + r.stderr)
    return CLI


def generate(
    n: int,
    op: str = "decaps",
    seed: str = "",
    invalid_random: bool = False,
    out_csv: str = "",
    quiet: bool = False,
) -> VectorSet:
    """Generate ``n`` shuffled fixed/random vectors with ``sca gen``.

    :param n: total number of traces, split equally between the classes.
    :param op: ``decaps`` or ``keygen``.
    :param seed: 64-byte hex seed fixing the key pair and the fixed input;
        random when empty. Record it to reproduce a campaign exactly.
    :param invalid_random: decaps only - the random class uses random
        (invalid) ciphertexts, which take the implicit-rejection path.
    :param out_csv: where to keep the CSV; a temporary file when empty.
    """
    cli = ensure_cli(quiet)
    tmp = not out_csv
    if tmp:
        import tempfile

        fd, out_csv = tempfile.mkstemp(prefix="tvla_vectors_", suffix=".csv")
        os.close(fd)
    cmd = [cli, "sca", "gen", "--op", op, "-n", str(n), "-o", out_csv]
    if seed:
        cmd += ["--seed", seed]
    if invalid_random and op == "decaps":
        cmd.append("--invalid-random")
    if not quiet:
        print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("sca gen failed:\n" + r.stdout + r.stderr)

    vs = load(out_csv, op=op)
    vs.seed = seed or _seed_from_output(r.stdout)
    vs.invalid_random = invalid_random
    vs.source = "pqc-testkit sca gen" if tmp else out_csv
    if tmp:
        os.unlink(out_csv)
    return vs


def _seed_from_output(text: str) -> str:
    """The seed `sca gen` prints, so a random campaign stays reproducible."""
    import re

    m = re.search(r"seed:?\s*([0-9a-fA-F]{32,})", text)
    return m.group(1) if m else ""


def load(path: str, op: str = "decaps") -> VectorSet:
    """Read a vector CSV written by ``sca gen``."""
    op_id = {"keygen": P.OP_KEYGEN, "encaps": P.OP_ENCAPS, "decaps": P.OP_DECAPS}[op]
    vectors = []
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise SystemExit("%s is empty" % path)
    start = 1 if rows[0] and not rows[0][0].strip().isdigit() else 0
    for row in rows[start:]:
        if len(row) < 3 or not row[0].strip():
            continue
        vectors.append(Vector(int(row[0]), int(row[1]), bytes.fromhex(row[2].strip())))
    if not vectors:
        raise SystemExit("%s holds no vectors" % path)

    want = {P.OP_DECAPS: P.DK_LEN + P.CT_LEN, P.OP_KEYGEN: 64}.get(op_id)
    got = len(vectors[0].data)
    if want and got != want:
        raise SystemExit(
            "%s: inputs are %d bytes, ML-KEM-768 %s expects %d" % (path, got, op, want)
        )
    return VectorSet(vectors, op_id, op, source=path)
