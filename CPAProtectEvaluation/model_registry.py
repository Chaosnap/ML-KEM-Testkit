"""Discovery and loading of CPA leakage-model modules.

A module qualifies as a "CPA model" by exposing the interface
``mlkem_ref.py`` implements: ``Q`` (modulus), ``K`` (polynomial count),
``TARGETS`` / ``RPREVS`` (dicts describing the guessable registers and the
R_prev choices), ``decrypt()`` and ``selftest()``. The GUI's "CPA Model" tab
discovers every ``*.py`` file in this directory that satisfies it, so a
second model (a masked-core variant, ML-KEM-512/1024, a different register
set) only needs to be dropped in next to ``mlkem_ref.py`` -- it does not
need to be wired into the GUI by hand.
"""

from __future__ import annotations

import csv
import importlib
import os
import pathlib
import subprocess

REQUIRED_ATTRS = ("Q", "K", "TARGETS", "RPREVS", "decrypt", "selftest")

#: Files in this directory that are the tool itself, never a candidate model.
_EXCLUDE = {
    "config.py", "gui_config_io.py", "web_gui.py", "model_registry.py",
    "acquire_cpa.py", "cpa_attack.py", "hdf5_trace_store.py", "scope.py", "uart_target.py",
    "tvla_analysis.py", "dev_regression_test.py",
}

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent  # ML-KEM-Testkit/, for the Go reference CLI
CLI = ROOT / "build" / "pqc-testkit"


def discover() -> list[str]:
    """Module names (no ``.py``) in this directory that look like a CPA model."""
    found = []
    for path in sorted(HERE.glob("*.py")):
        if path.name in _EXCLUDE or path.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(path.stem)
            mod = importlib.reload(mod)  # pick up on-disk edits without restarting the server
        except Exception:
            continue
        if all(hasattr(mod, a) for a in REQUIRED_ATTRS):
            found.append(path.stem)
    return found


def load(name: str):
    """Import (or re-import) ``name`` and validate it exposes the model interface."""
    if name in _EXCLUDE or not (HERE / (name + ".py")).is_file():
        raise ValueError("no such model module: %s" % name)
    mod = importlib.reload(importlib.import_module(name))
    missing = [a for a in REQUIRED_ATTRS if not hasattr(mod, a)]
    if missing:
        raise ValueError("%s is not a CPA model module (missing %s)" % (name, ", ".join(missing)))
    return mod


def describe(mod) -> dict:
    doc = (mod.__doc__ or "").strip().split("\n\n")[0].strip()
    # Optional: named (target, rprev, ...) shortcuts, a dict of dicts -- see
    # mlkem_ref.py's MODEL_PRESETS docstring for the field contract any
    # third-party model module should follow if it defines its own.
    presets = getattr(mod, "MODEL_PRESETS", {})
    return {
        "module": mod.__name__,
        "doc": doc,
        "q": int(mod.Q),
        "k": int(mod.K),
        "targets": [{"name": n, "bits": b} for n, b in mod.TARGETS.items()],
        "rprevs": [{"name": n, "desc": d} for n, d in mod.RPREVS.items()],
        "presets": [dict(name=n, **p) for n, p in presets.items()],
    }


def run_selftest(mod) -> dict:
    """Software-decrypt self-test against the Go reference (README section 3.2).

    Generates one fixed-class Decaps vector with ``pkg/sca`` (the same
    reference implementation the hardware is checked against) and verifies
    the model's own ``decrypt()`` reproduces the message it was built from.
    A model module that doesn't implement ``fixed_message()``/``DK_LEN``
    (the FIPS203 reference helpers) just reports that this check doesn't
    apply to it, rather than failing.
    """
    if not hasattr(mod, "fixed_message") or not hasattr(mod, "DK_LEN"):
        return {"ok": None, "detail": "%s has no fixed_message()/DK_LEN -- this self-test is "
                                      "specific to the FIPS203 reference helpers and doesn't "
                                      "apply to this module" % mod.__name__}
    if not CLI.is_file():
        return {"ok": False, "detail": "build/pqc-testkit not found -- build it first: "
                                       "cd %s && go build -o build/pqc-testkit ./cmd/pqc-testkit" % ROOT}
    seed = os.urandom(64)
    out_csv = "/tmp/cpa_model_selftest.csv"  # scratch only, overwritten every call
    r = subprocess.run([str(CLI), "sca", "gen", "--op", "decaps", "-n", "4",
                         "--seed", seed.hex(), "-o", out_csv], cwd=str(ROOT),
                        capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "detail": "pqc-testkit sca gen failed: %s" % r.stderr.strip()}
    rows = list(csv.DictReader(open(out_csv)))
    m0 = mod.fixed_message(seed)
    for row in rows:
        if row["class"] != "0":
            continue
        d = bytes.fromhex(row["input_hex"])
        dk, ct = d[: mod.DK_LEN], d[mod.DK_LEN :]
        ok = mod.selftest(dk, ct, m0)
        return {"ok": bool(ok), "detail": "software decrypt %s the Go reference's fixed message"
                                          % ("matches" if ok else "does NOT match -- NTT/BaseMul "
                                             "in this model is wrong, fix before trusting any CPA result")}
    return {"ok": False, "detail": "sca gen produced no fixed-class row (unexpected)"}
