#!/usr/bin/env python3
"""Trace acquisition for the ML-KEM-768 core (Decaps / BaseMul), two modes.

Drives the board and the scope and writes one HDF5 file per campaign; it does
not run an attack or a t-test -- run ``cpa_attack.py`` or ``tvla_analysis.py``
on the output file for that. Both modes keep ``dk`` fixed for the whole
campaign (a decapsulation oracle: the standard setup for both CPA and TVLA
against a KEM, Lee FDTC 2020 / Ravi et al. ToSC 2020).

What gets sent is a *test-vector set* (``testvectors.py``): the key pair and
every ciphertext with the shared secret the core must return. Every capture
is checked against that expected key, so a reset board, a corrupted link or
a wrong key shows up on the very trace it happens.

CPA mode: N different ciphertexts (``valid`` / ``invalid`` / ``structured``),
one trace each, for correlation against the leakage model in ``mlkem_ref.py``
(``docs/ATTACK_MODEL.md``). TVLA mode: fixed-vs-random (FIPS 203 Decaps TVLA
methodology), for Welch's t-test.

    python acquire_cpa.py ports                               # find the board
    python acquire_cpa.py check -p PORT                       # link self-test
    python acquire_cpa.py cpa-run  -p PORT --ip SCOPE_IP -n 20000 [--strategy valid]
    python acquire_cpa.py tvla-run -p PORT --ip SCOPE_IP -n 20000 [--invalid-random]
    python acquire_cpa.py cpa-run  -p PORT --ip SCOPE_IP --vectors data/vectors/<set>.npz
    python acquire_cpa.py resume   -p PORT --ip SCOPE_IP data/traces/<file>.hdf5
    python acquire_cpa.py summary  data/traces/<file>.hdf5

Without --ip, SCOPE_IP from config.py is used (a placeholder until edited).
Vector sets: ``python testvectors.py --help``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

import config as C
import mlkem768 as KEM
import scope as S
import testvectors as TV
import uart_target as T
from hdf5_trace_store import TraceReader, TraceWriter, summarize

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TRACES_DIR = os.path.join(ROOT, "data", "traces")


# --- the three BMUL windows, per sample-rate ------------------------------------
def cycle_to_sample(cycle: float, sample_rate_hz: float, record_start_s: float = 0.0) -> int:
    """Sample index of core clock cycle ``cycle`` after the trigger (the Decaps start).

    ``record_start_s`` is the time of sample 0 relative to the trigger, as the scope
    reports it (LeCroy HORIZ_OFFSET): negative when the record has pre-trigger data,
    e.g. -1 us with the trigger in the middle of a 2 us record. Without it (old
    campaigns, drivers that do not report it) sample 0 is taken as the trigger.
    """
    return int(round((cycle / (C.CORE_MHZ * 1e6) - (record_start_s or 0.0)) * sample_rate_hz))


def window_sample_ranges(sample_rate_hz: float, n_samples: int,
                         record_start_s: float = 0.0) -> list[tuple[int, int]]:
    """BMUL_WINDOWS (core cycles) mapped to sample indices, clipped to the trace,
    each widened by TRIGGER_SLACK_CYCLES core cycles on both sides.

    If sample_rate_hz is 0 (the scope preamble didn't report one), the cycle
    numbers are used unchanged, i.e. one sample per core cycle.
    """
    sr = sample_rate_hz if sample_rate_hz > 0 else C.CORE_MHZ * 1e6
    start = record_start_s if sample_rate_hz > 0 else 0.0
    out = []
    for c0, c1 in C.BMUL_WINDOWS:
        s0 = min(n_samples, max(0, cycle_to_sample(c0 - C.TRIGGER_SLACK_CYCLES, sr, start)))
        s1 = min(n_samples, max(0, cycle_to_sample(c1 + C.TRIGGER_SLACK_CYCLES, sr, start)))
        out.append((s0, max(s0, s1)))  # a window outside the trace is empty, never inverted
    return out


def dk_from_recipe(recipe: dict) -> bytes:
    """Rebuild a vector set's dk from its seed (KeyGen is deterministic)."""
    s64 = TV.seed64(bytes.fromhex(recipe["seed"]))
    return KEM.keygen(s64[:32], s64[32:])[1]


def measure_record_length(target, sc) -> int:
    """Samples per capture at the scope's current settings, from one Decaps of
    a fresh random vector (the capture is discarded). Used when neither the
    scope connection nor the campaign names a sample count."""
    vs = TV.generate("cpa", 1, os.urandom(64), "valid")
    sc.arm()
    out = target.decaps(vs.dk, vs.ct[0].tobytes(), resend_dk=True)
    if not out.ok:
        raise RuntimeError("could not measure the scope's record length: %s" % out.describe())
    return int(np.asarray(sc.read(timeout=10.0)).shape[0])


def run_campaign(target, sc, out_path: str, n_samples: int, vectors: TV.VectorSet | None = None,
                 resend_dk_every: int = 50, max_errors: int = 5, quiet: bool = False,
                 progress_cb=None, trace_cb=None, stop_cb=None, resume: bool = False,
                 vector_set_path: str = "") -> dict:
    """Capture one trace per vector; returns ``summarize(out_path)`` plus run counters.

    Fresh campaign: ``vectors`` is required; its ciphertexts, classes and
    expected keys are committed to the file before the first capture. Resume
    (``resume=True``): ``out_path`` already exists; everything is read back
    from it and only ``dk`` is rebuilt from the stored recipe's seed (its
    SHA3-256 must match the file's ``dk_hash``).

    Callbacks: ``progress_cb(done, total, message)`` after every trace;
    ``trace_cb(index, trace, cls, ok)`` with every captured trace (live
    preview); ``stop_cb()`` polled after every trace -- True stops early and
    leaves a valid, resumable partial file.

    Integrity checks on every trace: the output must equal the expected
    shared secret (FIPS 203, from the vector set), and CYCLE_COUNT must stay
    identical (the core is supposed to be constant-time; a change is itself a
    timing-leak finding). A link that is gone (port closed, USB unplugged)
    ends the campaign at once; it can be resumed.
    """
    target.require_run()  # a bitstream without 0x05 Run cannot run operations: fail before any file
    if resume:
        writer = TraceWriter.open_existing(out_path)
        recipe = json.loads(str(writer.f["metadata"].attrs.get("recipe", "{}")))
        if "seed" not in recipe:
            writer.close()
            raise ValueError("%s has no recipe seed -- cannot rebuild dk to resume" % out_path)
        dk = dk_from_recipe(recipe)
        if hashlib.sha3_256(dk).digest() != bytes(writer.f["intermediates/dk_hash"][:]):
            writer.close()
            raise ValueError("rebuilt dk does not match the file's dk_hash -- refusing to resume")
        mode, n = writer.mode, writer.n_traces
        start_index = writer.n_completed
        if start_index >= n:
            writer.close()
            return summarize(out_path)
    else:
        if vectors is None:
            raise ValueError("a fresh campaign needs a test-vector set")
        dk, mode, n = vectors.dk, vectors.mode, vectors.n
        meta = {
            "mode": mode, "strategy": vectors.meta["strategy"], "op": "decaps",
            "model": "ML-KEM-768 BaseMul, HW(u * g) product hypotheses (docs/LEAKAGE_MODELS.md)",
            "n_traces": n, "core_mhz": C.CORE_MHZ, "bmul_windows_cycles": json.dumps(C.BMUL_WINDOWS),
            "scope": json.dumps(sc.info()), "board": json.dumps(target.info()),
            "simulated": False, "created_unix": time.time(),
            "recipe": json.dumps(vectors.meta), "vector_set": vector_set_path or vectors.meta["id"],
        }
        writer = TraceWriter(out_path, n, n_samples, dk, meta, mode=mode,
                             items=vectors.campaign_items(), expected=vectors.k)
        start_index = 0

    errors = k_mismatches = cycle_changes = 0
    first_cycles: int | None = None
    t_start = time.time()
    stopped_early = link_lost = False
    try:
        for i in range(start_index, n):
            ct = writer.ct_at(i)
            cls = writer.cls_at(i) if mode == "tvla" else None
            expected = writer.expected_at(i)
            resend_dk = (i == start_index) or (i % resend_dk_every == 0)

            sc.arm()
            out = target.decaps(dk, ct, resend_dk=resend_dk)
            if out.link_fatal:
                link_lost = stopped_early = True
                if progress_cb:
                    progress_cb(i, n, "link lost: %s" % out.link_error)
                break
            if not out.ok:
                errors += 1
                if not quiet:
                    print("  [%d/%d] core error: %s (resync...)" % (i + 1, n, out.describe()))
                target.link.resync()
                if errors > max_errors:
                    raise RuntimeError("too many consecutive core errors (%d); stopped after %d "
                                       "traces -- the file is resumable" % (errors, i))
                writer.append(i, np.zeros(n_samples, dtype=np.float32), ct, out.cycles, out.output,
                              ok=False, cls=cls)
                if progress_cb:
                    progress_cb(i + 1, n, "core error, resynced")
                if stop_cb and stop_cb():
                    stopped_early = True
                    break
                continue
            errors = 0

            trace = sc.read(timeout=10.0)
            if trace.shape[0] != n_samples:
                # timebase / memory depth changed: the rows would no longer line up in time
                raise RuntimeError("the scope returned %d samples but this campaign records %d per trace: the "
                                   "timebase or memory depth changed -- set it back and resume (%d traces kept)"
                                   % (trace.shape[0], n_samples, i))

            ok = True
            if expected is not None and out.output != expected:
                ok = False
                k_mismatches += 1
                target.forget_config()  # e.g. BTN0 reset the CSRs: rewrite them next trace
                if not quiet:
                    print("  [%d/%d] WARNING: output K differs from the FIPS 203 expected value -- "
                          "board reset, wrong key or corrupted link" % (i + 1, n))
            if first_cycles is None:
                first_cycles = out.cycles
            elif out.cycles != first_cycles:
                cycle_changes += 1
                if not quiet:
                    print("  [%d/%d] WARNING: CYCLE_COUNT varied (%d vs first %d) -- the core is not "
                          "constant-time for this input" % (i + 1, n, out.cycles, first_cycles))
            writer.append(i, trace, ct, out.cycles, out.output, ok=ok, cls=cls)
            if trace_cb:
                trace_cb(i, trace, cls, ok)
            if progress_cb:
                progress_cb(i + 1, n, out.describe() + ("" if ok else "  [K MISMATCH]"))

            if not quiet and (i % max(1, n // 20) == 0 or i == n - 1):
                elapsed = time.time() - t_start
                rate = (i + 1 - start_index) / elapsed if elapsed > 0 else 0.0
                print("  [%d/%d] %s  (%.1f traces/s, eta %.0f s)"
                      % (i + 1, n, out.describe(), rate, (n - i - 1) / max(rate, 1e-9)))

            if stop_cb and stop_cb():
                stopped_early = True
                break
    finally:
        query = getattr(sc, "record_start_s", None)
        writer.set_sample_rate(sc.sample_rate(), query() if callable(query) else None)
        writer.close()
    summary = summarize(out_path)
    summary.update(stopped_early=stopped_early, link_lost=link_lost, k_mismatches=k_mismatches,
                   cycle_changes=cycle_changes)
    return summary


# --- CLI ------------------------------------------------------------------------
def cmd_ports(args):
    for dev, desc, hint, rec in T.list_ports():
        print("%s%s - %s  %s" % ("* " if rec else "  ", dev, desc, hint))


def cmd_scopes(args):
    for res, idn in S.list_resources():
        print(res, "-", idn)


def _open_target(port: str) -> T.Target:
    try:
        return T.Target.open(port)
    except T.LinkError as e:
        raise SystemExit(str(e))


def cmd_check(args):
    target = _open_target(args.port)
    try:
        print("info:", target.info())
        for name, ok, detail in target.preflight():
            print(("  OK  " if ok else "  FAIL"), name, "--", detail)
    finally:
        target.close()


def _open_board_and_scope(args):
    target = _open_target(args.port)
    checks = target.preflight()
    if not all(ok for _, ok, _ in checks):
        for name, ok, detail in checks:
            print(("  OK  " if ok else "  FAIL"), name, "--", detail)
        target.close()
        raise SystemExit("preflight failed; fix the link before capturing")
    ip = args.ip or C.SCOPE_IP
    if ip == C.SCOPE_PLACEHOLDER_IP:
        print("WARNING: SCOPE_IP is still the config.py placeholder (%s) -- edit config.py or "
              "pass --ip <real address>." % ip, file=sys.stderr)
    print("connecting to scope at", S.resource_for(ip, args.transport), "...")
    sc = S.open_scope(ip, channel=args.channel, dialect=args.dialect, points=args.samples,
                      transport=args.transport)
    sc.configure()
    print("scope:", sc.info())
    return target, sc


def _vectors_for(args, mode: str) -> tuple[TV.VectorSet, str]:
    if args.vectors:
        vs = TV.VectorSet.load(args.vectors)
        if vs.mode != mode:
            raise SystemExit("%s is a %s vector set, not %s" % (args.vectors, vs.mode, mode))
        return vs, str(args.vectors)
    seed = bytes.fromhex(args.seed) if args.seed else os.urandom(64)
    print("seed: %s  (pass --seed to reproduce)" % seed.hex())
    if mode == "cpa":
        vs = TV.generate("cpa", args.traces, seed, args.strategy, args.poly, args.pair)
    else:
        vs = TV.generate("tvla", args.traces, seed, invalid_random=args.invalid_random)
    path = vs.save()
    print("vector set:", path)
    return vs, str(path)


def _run(args, mode: str):
    vs, vs_path = _vectors_for(args, mode)
    target, sc = _open_board_and_scope(args)
    n_samples = sc.samples or args.samples or measure_record_length(target, sc)
    print("%d samples per trace" % n_samples)
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S") + "_%s.hdf5" % mode)
    print("writing", out)
    try:
        summary = run_campaign(target, sc, out, n_samples, vectors=vs, vector_set_path=vs_path,
                               resend_dk_every=args.resend_dk_every, quiet=args.quiet)
    finally:
        sc.close()
        target.close()
    print(json.dumps(summary, indent=2, default=str))


def cmd_resume(args):
    with TraceReader(args.file) as r:
        n_samples = r.f["traces"].shape[1]
    target, sc = _open_board_and_scope(args)
    try:
        summary = run_campaign(target, sc, args.file, n_samples, resume=True,
                               resend_dk_every=args.resend_dk_every, quiet=args.quiet)
    finally:
        sc.close()
        target.close()
    print(json.dumps(summary, indent=2, default=str))


def cmd_summary(args):
    print(json.dumps(summarize(args.file), indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial ports (* = recommended)").set_defaults(fn=cmd_ports)
    sub.add_parser("scopes", help="list VISA resources currently visible").set_defaults(fn=cmd_scopes)

    sp = sub.add_parser("check", help="board link self-test (no scope needed)")
    sp.add_argument("-p", "--port", required=True)
    sp.set_defaults(fn=cmd_check)

    def add_hw(sp):
        sp.add_argument("-p", "--port", required=True)
        sp.add_argument("--ip", default="", help="scope IP; default is SCOPE_IP in config.py")
        sp.add_argument("--channel", default="", help="default is SCOPE_CHANNEL in config.py (LeCroy: C1..C4)")
        sp.add_argument("--dialect", default="", choices=("",) + S.DIALECT_NAMES)
        sp.add_argument("--transport", default="", choices=[""] + list(C.SCOPE_RESOURCES),
                        help="default is SCOPE_TRANSPORT in config.py (vicp / vxi11 / socket)")
        sp.add_argument("--samples", type=int, default=0, help="0 = the scope's record length, measured with one capture")
        sp.add_argument("--resend-dk-every", type=int, default=50)
        sp.add_argument("--quiet", action="store_true")

    def add_vectors(sp):
        sp.add_argument("--vectors", default="", help="an existing vector set (.npz); else one is generated")
        sp.add_argument("-n", "--traces", type=int, default=5000)
        sp.add_argument("--seed", default="", help="hex; random if empty")
        sp.add_argument("--out", default=TRACES_DIR)

    sp = sub.add_parser("cpa-run", help="CPA campaign on the board + scope")
    add_hw(sp)
    add_vectors(sp)
    sp.add_argument("--strategy", default="valid", choices=list(TV.STRATEGIES))
    sp.add_argument("--poly", type=int, default=0, choices=[0, 1, 2], help="structured strategy only")
    sp.add_argument("--pair", type=int, default=0, help="structured strategy only, 0..127")
    sp.set_defaults(fn=lambda a: _run(a, "cpa"))

    sp = sub.add_parser("tvla-run", help="TVLA (fixed-vs-random) campaign on the board + scope")
    add_hw(sp)
    add_vectors(sp)
    sp.add_argument("--invalid-random", action="store_true",
                    help="random class = random bytes (implicit-rejection path)")
    sp.set_defaults(fn=lambda a: _run(a, "tvla"))

    sp = sub.add_parser("resume", help="continue a stopped campaign file")
    add_hw(sp)
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_resume)

    sp = sub.add_parser("summary", help="print an HDF5 campaign file's metadata")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_summary)

    args = p.parse_args()
    if getattr(args, "out", None):
        Path(args.out).mkdir(parents=True, exist_ok=True)
    args.fn(args)


if __name__ == "__main__":
    main()
