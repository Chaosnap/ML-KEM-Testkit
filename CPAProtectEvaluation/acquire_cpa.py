#!/usr/bin/env python3
"""Trace acquisition for the ML-KEM-768 core (Decaps / BaseMul), two modes.

This script drives the board and the scope and writes an HDF5 file; it does
NOT run an attack or a t-test -- run ``cpa_attack.py`` or
``tvla_analysis.py`` on the output file for that. Both modes keep ``dk``
fixed for the whole campaign (a decapsulation oracle: the standard setup for
both CPA and TVLA against a KEM, Lee FDTC 2020 / Ravi et al. ToSC 2020).

CPA mode (``cpa-dry-run`` / ``cpa-run``): N *different* ciphertexts, one
trace per ciphertext, for correlation-based key recovery against the
leakage model in ``mlkem_ref.py`` / ``ATTACK_MODEL.md``. Ciphertext
``--strategy``:

  valid       fresh, FIPS203-valid encapsulations (default)
  invalid     random bytes as ciphertext (fails the FO check; exercises the
              implicit-rejection path, u_hat is still whatever those random
              bytes decode to)
  structured  chosen ciphertext isolating one BaseMul pair (--poly/--pair):
              every *other* pair's u_hat is zero, raising SNR at that one
              pair -- see mlkem_ref.py: structured_ciphertext()

TVLA mode (``tvla-dry-run`` / ``tvla-run``): fixed-vs-random Welch's t-test
vectors from ``pkg/sca`` (FIPS203 Decaps TVLA methodology: same dk, class 0
= one fixed ciphertext repeated, class 1 = a fresh valid encapsulation each
time, or random bytes with --invalid-random) -- for ``tvla_analysis.py``.

    python acquire_cpa.py ports                                 # find the board
    python acquire_cpa.py check -p PORT                          # link self-test
    python acquire_cpa.py cpa-dry-run -n 300                      # no scope, no board
    python acquire_cpa.py cpa-dry-run -n 300 --leak-mv 4            # positive control
    python acquire_cpa.py cpa-run -p PORT --ip SCOPE_IP -n 20000
    python acquire_cpa.py tvla-dry-run -n 300
    python acquire_cpa.py tvla-run -p PORT --ip SCOPE_IP -n 20000

Without --ip, SCOPE_IP from config.py is used; the default there is a
placeholder (192.0.2.100, a reserved address that can never answer) -- edit
config.py or pass --ip once the real instrument is on the network.

Every file this script writes records ``simulated`` in its metadata (True
for *-dry-run, False for *-run). A dry-run file is software validation of
this pipeline, never evidence about the real core -- see README.md section 6.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

import config as C
import mlkem_ref as R
import scope as S
import uart_target as T
from hdf5_trace_store import TraceWriter, summarize

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "pqc-testkit")


def ensure_cli() -> str:
    if os.path.exists(CLI):
        return CLI
    raise SystemExit(
        "build/pqc-testkit not found. Build it first:\n"
        "  cd %s && go build -o build/pqc-testkit ./cmd/pqc-testkit" % ROOT
    )


def _sca_gen(seed: bytes, n: int, invalid_random: bool = False) -> tuple[bytes, list]:
    """Run ``pqc-testkit sca gen --op decaps`` and return ``(dk, rows)``.

    ``rows`` is the raw list of ``(class, ciphertext)`` pairs in the file's
    (shuffled) order -- class 0 is the one fixed ciphertext, class 1 is a
    fresh valid encapsulation each time (or random bytes with
    ``invalid_random``). Shared by both cpa_ciphertexts() (which only keeps
    the class-1 rows) and tvla_vectors() (which keeps the labels).
    """
    ensure_cli()
    n_even = max(n + (n % 2), 4)  # sca gen requires an even, >=4 total
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "v.csv")
        cmd = [CLI, "sca", "gen", "--op", "decaps", "-n", str(n_even), "--seed", seed.hex(), "-o", out]
        if invalid_random:
            cmd.append("--invalid-random")
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("pqc-testkit sca gen failed:\n" + r.stderr)
        rows = list(csv.DictReader(open(out)))
    dk = None
    out_rows = []
    for row in rows:
        data = bytes.fromhex(row["input_hex"])
        d, ct = data[: T.DK_LEN], data[T.DK_LEN :]
        dk = dk or d
        out_rows.append((int(row["class"]), ct))
    if dk is None:
        raise SystemExit("sca gen produced no vectors")
    return dk, out_rows


# --- CPA mode: ciphertext strategies ------------------------------------------
def cpa_ciphertexts(seed: bytes, n: int, strategy: str = "valid",
                     poly: int = 0, pair: int = 0) -> tuple[bytes, list[bytes], bytes]:
    """``dk`` (fixed) and ``n`` ciphertexts per ``strategy``, plus a KAT ciphertext.

    "valid": the sca-gen random class (fresh valid encapsulation each time).
    "invalid": the sca-gen random class with --invalid-random (random bytes).
    "structured": mlkem_ref.structured_ciphertext(poly, pair, ...), isolating
    one BaseMul pair; dk still comes from sca gen (only 4 vectors requested,
    the ciphertexts themselves are discarded) so the real FIPS203 key
    derivation is used, not an ad-hoc one.
    """
    if strategy not in ("valid", "invalid", "structured"):
        raise ValueError("strategy must be valid, invalid or structured, got %r" % strategy)

    if strategy == "structured":
        dk, rows = _sca_gen(seed, 4)  # only need dk; discard these 4 ciphertexts
        rng = np.random.default_rng(int.from_bytes(seed[:8], "little"))
        cts = [R.structured_ciphertext(poly, pair, rng) for _ in range(n)]
        fixed_ct = next(ct for cls, ct in rows if cls == 0)
        return dk, cts, fixed_ct

    dk, rows = _sca_gen(seed, n, invalid_random=(strategy == "invalid"))
    cts = [ct for cls, ct in rows if cls == 1]
    fixed_ct = next(ct for cls, ct in rows if cls == 0)
    base = len(cts)
    if base == 0:
        raise SystemExit("sca gen produced no random-class ciphertexts")
    while len(cts) < n:  # n odd: pad by cycling through the `base` distinct ones
        cts.append(cts[len(cts) % base])
    return dk, cts[:n], fixed_ct


# --- TVLA mode: native fixed/random vectors from pkg/sca ----------------------
def tvla_vectors(seed: bytes, n: int, invalid_random: bool = False) -> tuple[bytes, list[tuple[int, bytes]]]:
    """``dk`` and ``n`` ``(class, ciphertext)`` pairs, class 0=fixed/1=random.

    This *is* the FIPS203 Decaps TVLA vector set pkg/sca already generates
    (GenerateMLKEMTVLA) -- no separate TVLA generator needed, just don't
    discard the class-0 rows the way cpa_ciphertexts() does.
    """
    return _sca_gen(seed, n, invalid_random=invalid_random)


# --- the three windows, per sample-rate --------------------------------------
def cycle_to_sample(cycle: int, sample_rate_hz: float) -> int:
    return int(round(cycle / C.CORE_MHZ / 1e6 * sample_rate_hz))


def window_sample_ranges(sample_rate_hz: float, n_samples: int) -> list[tuple[int, int]]:
    """BMUL_WINDOWS (core cycles) mapped to sample indices, clipped to the trace.

    If sample_rate_hz is 0 (e.g. the scope preamble didn't report one, or a
    synthetic run), returns the cycle numbers unchanged -- fine as long as
    the "sample rate" used for capture equals the core clock in cycles/s,
    which is what the synthetic scope and a 1:1 digital capture would give.
    """
    sr = sample_rate_hz if sample_rate_hz > 0 else C.CORE_MHZ * 1e6
    out = []
    for c0, c1 in C.BMUL_WINDOWS:
        s0 = max(0, cycle_to_sample(c0, sr) - C.TRIGGER_SLACK_CYCLES)
        s1 = min(n_samples, cycle_to_sample(c1, sr) + C.TRIGGER_SLACK_CYCLES)
        out.append((s0, s1))
    return out


def _inject_synthetic_leak(sc: S.SyntheticScope, dk: bytes, ct: bytes) -> None:
    """Point the SyntheticScope at the HD(prev, word) leak of this ciphertext.

    Shared by both modes' dry-run paths: the "core operation" being
    simulated is the same Decaps regardless of which mode is acquiring, so
    the same leak model applies to a trace from either one.
    """
    s_hat = R.s_hat_from_dk(dk)
    u_hat = R.u_hat_from_ct(np.frombuffer(ct, dtype=np.uint8)[None, :])[0]
    centres, hws = [], []
    for poly, (c0, c1) in enumerate(C.BMUL_WINDOWS):
        words, prev = R.window_words(u_hat, s_hat, poly)
        idx = (c0 + np.linspace(0, c1 - c0, len(words), endpoint=False)).astype(int)
        centres.extend(idx.tolist())
        hws.extend(R.hamming_distance(prev, words, 24).tolist())
    sc.set_leak(centres, np.asarray(hws, dtype=np.float64))


# --- acquisition loop ---------------------------------------------------------
def run_campaign(target, sc, dk: bytes, items: list, out_path: str, n_samples: int,
                  mode: str = "cpa", strategy: str = "", resend_dk_every: int = 50,
                  max_errors: int = 5, quiet: bool = False, progress_cb=None,
                  recipe: dict | None = None, resume: bool = False,
                  stop_cb=None) -> dict:
    """Capture one trace per item; returns a summary dict.

    ``progress_cb(completed, total, message)``, if given, is called after
    every trace (used by the GUI's background acquisition job to report
    live progress; the CLI doesn't pass one and nothing changes for it).

    ``items`` is ``list[bytes]`` (ciphertexts) for ``mode="cpa"``, or
    ``list[(cls, bytes)]`` for ``mode="tvla"``. On a fresh (non-resume) call
    this is written into the file's ``/intermediates/ct`` (and ``class``)
    for every row immediately, before any trace is captured -- the loop
    below then reads each row's ciphertext/class back from the file
    (``writer.ct_at(i)`` / ``writer.cls_at(i)``), never from ``items``
    directly. This matters for resume: regenerating ``items`` a second time
    from the same seed is NOT reliably reproducible -- ``pqc-testkit sca
    gen``'s random/valid-class ciphertexts and shuffle order use real OS
    randomness, only ``dk`` itself is seed-deterministic (confirmed the hard
    way: dev_regression_test.py's resume test originally diverged because
    it assumed otherwise). So on ``resume=True``, ``items`` only needs to
    have the right LENGTH (sanity-checked below); its ciphertext content is
    never used -- the rows already committed to the file are the only
    source of truth for what to (re-)send to the hardware.

    ``recipe``, if given, is persisted into the file's metadata as the
    parameters (seed/strategy/poly/pair/invalid_random) that produced
    ``items``, purely for provenance/display -- NOT relied on to regenerate
    byte-identical ciphertexts (see above). ``resume=True`` reopens
    ``out_path`` (which must already exist, from an earlier call) and
    continues from its current ``n_completed`` instead of starting a fresh
    file at index 0.

    ``stop_cb``, if given, is polled after every trace; when it returns
    True the loop stops early (used by the GUI's Stop button) -- whatever
    was captured so far is still a valid, resumable partial file (see
    TraceWriter: every row is flushed to its own index as it is written, and
    ``n_completed`` always reflects exactly how many rows are valid).

    Two integrity checks run for free, both from ``docs/TVLA_SIM.md`` /
    the hostlab README's "what can go wrong during a multi-hour capture"
    section: CYCLE_COUNT must be identical on every trace (the core is
    supposed to be constant-time; a change is itself a timing-leak finding,
    not just a bad trace), and repeating a known ciphertext must keep
    returning the same output K (catches a silently reset board or a
    corrupted link without needing a full KEM re-implementation here).
    """
    n = len(items)
    is_synthetic = isinstance(sc, S.SyntheticScope)
    start_index = 0
    if resume:
        writer = TraceWriter.open_existing(out_path)
        if writer.n_traces != n:
            writer.close()
            raise SystemExit("resume: recipe now produces %d items but the file was planned for "
                             "%d -- the recipe stored in the file's metadata doesn't match what "
                             "was just regenerated (changed seed/strategy/poly/pair?)" % (n, writer.n_traces))
        start_index = writer.n_completed
        if start_index >= n:
            writer.close()
            return summarize(out_path)  # already complete; nothing to do
    else:
        meta = {
            "mode": mode, "strategy": strategy, "op": "decaps",
            "model": "ML-KEM-768 BaseMul, HD(R_prev, X) leakage model (see ATTACK_MODEL.md)",
            "n_traces": n, "core_mhz": C.CORE_MHZ, "bmul_windows_cycles": json.dumps(C.BMUL_WINDOWS),
            "scope": json.dumps(sc.info()), "board": json.dumps(target.info()),
            "simulated": target.simulated, "created_unix": time.time(),
        }
        if recipe:
            meta["recipe"] = json.dumps(recipe)  # {seed, strategy, poly, pair, invalid_random} -- provenance only
        writer = TraceWriter(out_path, n, n_samples, dk, meta, mode=mode, items=items)
    errors = 0
    first_cycles: int | None = None
    kat_ct = writer.ct_at(0)  # row 0, as actually committed -- stable across a stop/resume boundary
    kat_output = None
    t_start = time.time()
    stopped_early = False
    try:
        for i in range(start_index, n):
            ct = writer.ct_at(i)
            cls = writer.cls_at(i) if mode == "tvla" else None
            resend_dk = (i % resend_dk_every == 0)
            use_ct = kat_ct if (mode == "cpa" and i > 0 and i % 25 == 0) else ct

            if is_synthetic:
                _inject_synthetic_leak(sc, dk, use_ct)

            sc.arm()
            out = target.decaps(dk, use_ct, resend_dk=resend_dk)
            if not out.ok and not target.simulated:
                errors += 1
                if not quiet:
                    print("  [%d/%d] core error: %s (resync...)" % (i + 1, n, out.describe()))
                if hasattr(target.link, "resync"):
                    target.link.resync()
                if errors > max_errors:
                    raise SystemExit("too many consecutive core errors (%d); aborting, "
                                     "%d traces captured" % (errors, i))
                writer.append(i, np.zeros(n_samples, dtype=np.float32), use_ct, out.cycles, out.output,
                              ok=False, cls=cls)
                if progress_cb:
                    progress_cb(i + 1, n, "core error, retrying")
                if stop_cb and stop_cb():
                    stopped_early = True
                    break
                continue
            errors = 0

            trace = sc.read(timeout=10.0)
            if trace.shape[0] != n_samples:
                trace = np.resize(trace, n_samples).astype(np.float32)

            ok = True
            if first_cycles is None:
                first_cycles = out.cycles
            elif out.cycles != first_cycles and not quiet:
                print("  [%d/%d] WARNING: CYCLE_COUNT varied (%d vs first %d) -- the core is "
                      "not constant-time for this input; this is itself a timing-leak finding"
                      % (i + 1, n, out.cycles, first_cycles))
            # Known-ciphertext repeat check: CPA mode substitutes kat_ct every
            # 25th trace (see `use_ct` above); TVLA mode's class-0 rows are
            # already a fixed ciphertext repeated by construction (pkg/sca),
            # so the same check applies to every cls==0 row for free.
            is_kat_repeat = (mode == "cpa" and use_ct is kat_ct) or (mode == "tvla" and cls == 0)
            if is_kat_repeat:
                if kat_output is None:
                    kat_output = out.output
                elif out.output != kat_output:
                    ok = False
                    if not quiet:
                        print("  [%d/%d] WARNING: known-ciphertext output changed -- board reset, "
                              "key changed, or link silently corrupted data; investigate before "
                              "trusting traces captured after this point" % (i + 1, n))
            writer.append(i, trace, use_ct, out.cycles, out.output, ok=ok, cls=cls)
            if progress_cb:
                progress_cb(i + 1, n, out.describe())

            if not quiet and (i % max(1, n // 20 or 1) == 0 or i == n - 1):
                elapsed = time.time() - t_start
                rate = (i + 1 - start_index) / elapsed if elapsed > 0 else 0.0
                print("  [%d/%d] %s  (%.1f traces/s, eta %.0f s)"
                      % (i + 1, n, out.describe(), rate, (n - i - 1) / max(rate, 1e-9)))

            if stop_cb and stop_cb():
                stopped_early = True
                break
    finally:
        writer.set_sample_rate(sc.sample_rate())
        writer.close()
    summary = summarize(out_path)
    summary["stopped_early"] = stopped_early
    return summary


# --- CLI ----------------------------------------------------------------------
def cmd_ports(args):
    for dev, desc in T.list_ports():
        print(dev, "-", desc)


def cmd_scopes(args):
    for res, idn in S.list_resources():
        print(res, "-", idn)


def cmd_check(args):
    target = T.Target.open(args.port)
    try:
        print("info:", target.info())
        for name, ok, detail in target.preflight():
            print(("  OK  " if ok else "  FAIL"), name, "--", detail)
    finally:
        target.close()


def _seed(args) -> bytes:
    seed = bytes.fromhex(args.seed) if args.seed else os.urandom(64)
    print("seed: %s  (pass --seed to reproduce)" % seed.hex())
    return seed


def cmd_cpa_dry_run(args):
    """No board, no scope: SimTarget + SyntheticScope end-to-end."""
    seed = _seed(args)
    dk, cts, _fixed_ct = cpa_ciphertexts(seed, args.traces, args.strategy, args.poly, args.pair)
    target = T.SimTarget()
    n_samples = args.samples or 6000
    sc = S.SyntheticScope(n_samples, noise_mv=args.noise_mv, leak_mv=args.leak_mv, seed=1)
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S") + "_cpa_dryrun.hdf5")
    print("writing", out)
    summary = run_campaign(target, sc, dk, cts, out, n_samples, mode="cpa", strategy=args.strategy,
                            quiet=args.quiet)
    print(json.dumps(summary, indent=2, default=str))
    if args.leak_mv == 0:
        print("\nleak_mv=0: this is the negative control. Run cpa_attack.py on this file -- "
              "it must report chance-level ranks, not a confident key.")
    else:
        print("\nleak_mv>0: this is the positive control. Run cpa_attack.py on this file -- "
              "it should recover the true key with a small trace count.")


def cmd_cpa_run(args):
    """Real board + real scope, CPA mode."""
    seed = _seed(args)
    dk, cts, _fixed_ct = cpa_ciphertexts(seed, args.traces, args.strategy, args.poly, args.pair)
    target, sc = _open_board_and_scope(args)
    n_samples = sc.samples or args.samples or 10000
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S") + "_cpa.hdf5")
    print("writing", out)
    try:
        summary = run_campaign(target, sc, dk, cts, out, n_samples, mode="cpa", strategy=args.strategy,
                                resend_dk_every=args.resend_dk_every, quiet=args.quiet)
    finally:
        sc.close()
        target.close()
    print(json.dumps(summary, indent=2, default=str))


def cmd_tvla_dry_run(args):
    seed = _seed(args)
    dk, rows = tvla_vectors(seed, args.traces, invalid_random=args.invalid_random)
    target = T.SimTarget()
    n_samples = args.samples or 6000
    sc = S.SyntheticScope(n_samples, noise_mv=args.noise_mv, leak_mv=args.leak_mv, seed=1)
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S") + "_tvla_dryrun.hdf5")
    print("writing", out)
    strategy = "invalid-random" if args.invalid_random else "valid"
    summary = run_campaign(target, sc, dk, rows, out, n_samples, mode="tvla", strategy=strategy,
                            quiet=args.quiet)
    print(json.dumps(summary, indent=2, default=str))
    print("\nRun tvla_analysis.py on this file to compute Welch's t-test.")


def cmd_tvla_run(args):
    seed = _seed(args)
    dk, rows = tvla_vectors(seed, args.traces, invalid_random=args.invalid_random)
    target, sc = _open_board_and_scope(args)
    n_samples = sc.samples or args.samples or 10000
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S") + "_tvla.hdf5")
    print("writing", out)
    strategy = "invalid-random" if args.invalid_random else "valid"
    try:
        summary = run_campaign(target, sc, dk, rows, out, n_samples, mode="tvla", strategy=strategy,
                                resend_dk_every=args.resend_dk_every, quiet=args.quiet)
    finally:
        sc.close()
        target.close()
    print(json.dumps(summary, indent=2, default=str))


def _open_board_and_scope(args):
    target = T.Target.open(args.port)
    checks = target.preflight()
    if not all(ok for _, ok, _ in checks):
        for name, ok, detail in checks:
            print(("  OK  " if ok else "  FAIL"), name, "--", detail)
        raise SystemExit("preflight failed; fix the link before capturing (see README.md)")

    ip = args.ip or C.SCOPE_IP
    if ip == C.SCOPE_PLACEHOLDER_IP:
        print("WARNING: SCOPE_IP is still the config.py placeholder (%s, RFC 5737 "
              "TEST-NET-1 -- reserved, never a real instrument). Edit config.py or "
              "pass --ip <real address>." % ip, file=sys.stderr)
    resource = S.resource_for(ip)
    print("connecting to scope at", resource, "...")
    sc = S.VisaScope(resource, channel=args.channel or C.SCOPE_CHANNEL,
                      points=args.samples, dialect=args.dialect or C.SCOPE_DIALECT)
    sc.configure()
    print("scope:", sc.info())
    return target, sc


def cmd_summary(args):
    print(json.dumps(summarize(args.file), indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list serial ports").set_defaults(fn=cmd_ports)
    sub.add_parser("scopes", help="list VISA resources currently visible").set_defaults(fn=cmd_scopes)

    sp = sub.add_parser("check", help="board link self-test (no scope needed)")
    sp.add_argument("-p", "--port", required=True)
    sp.set_defaults(fn=cmd_check)

    def add_common(sp, default_n):
        sp.add_argument("-n", "--traces", type=int, default=default_n)
        sp.add_argument("--seed", default="")
        sp.add_argument("--out", default="../sim_result/traces")
        sp.add_argument("--quiet", action="store_true")

    def add_scope_args(sp):
        sp.add_argument("--ip", default="", help="scope IP; default is SCOPE_IP in config.py")
        sp.add_argument("--channel", default="")
        sp.add_argument("--dialect", default="", choices=["", "default", "tektronix"])
        sp.add_argument("--samples", type=int, default=0, help="0 = scope's current memory depth")
        sp.add_argument("--resend-dk-every", type=int, default=50)

    sp = sub.add_parser("cpa-dry-run", help="CPA mode, no board/scope: exercise the full pipeline")
    add_common(sp, 300)
    sp.add_argument("--samples", type=int, default=0)
    sp.add_argument("--noise-mv", type=float, default=5.0)
    sp.add_argument("--leak-mv", type=float, default=0.0,
                     help="0 = negative control (pure noise); >0 = positive control (synthetic leak)")
    sp.add_argument("--strategy", default="valid", choices=["valid", "invalid", "structured"])
    sp.add_argument("--poly", type=int, default=0, choices=[0, 1, 2], help="structured strategy only")
    sp.add_argument("--pair", type=int, default=0, help="structured strategy only, 0..127")
    sp.set_defaults(fn=cmd_cpa_dry_run)

    sp = sub.add_parser("cpa-run", help="CPA mode, real board + real scope")
    sp.add_argument("-p", "--port", required=True)
    add_common(sp, 5000)
    add_scope_args(sp)
    sp.add_argument("--strategy", default="valid", choices=["valid", "invalid", "structured"])
    sp.add_argument("--poly", type=int, default=0, choices=[0, 1, 2], help="structured strategy only")
    sp.add_argument("--pair", type=int, default=0, help="structured strategy only, 0..127")
    sp.set_defaults(fn=cmd_cpa_run)

    sp = sub.add_parser("tvla-dry-run", help="TVLA mode, no board/scope: exercise the full pipeline")
    add_common(sp, 300)
    sp.add_argument("--samples", type=int, default=0)
    sp.add_argument("--noise-mv", type=float, default=5.0)
    sp.add_argument("--leak-mv", type=float, default=0.0)
    sp.add_argument("--invalid-random", action="store_true",
                     help="random class = random bytes (implicit-rejection path) instead of a fresh valid ct")
    sp.set_defaults(fn=cmd_tvla_dry_run)

    sp = sub.add_parser("tvla-run", help="TVLA mode, real board + real scope")
    sp.add_argument("-p", "--port", required=True)
    add_common(sp, 5000)
    add_scope_args(sp)
    sp.add_argument("--invalid-random", action="store_true")
    sp.set_defaults(fn=cmd_tvla_run)

    sp = sub.add_parser("summary", help="print an HDF5 campaign file's metadata")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_summary)

    args = p.parse_args()
    if hasattr(args, "out"):
        os.makedirs(args.out, exist_ok=True)
    args.fn(args)


if __name__ == "__main__":
    main()
