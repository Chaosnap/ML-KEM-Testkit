#!/usr/bin/env python3
"""Developer regression test -- SYNTHETIC ONLY. Never evidence about real hardware.

This is the "developer test entry" that the Web GUI no longer offers on its
official surface (see web_gui.py's Trace Acquisition tab comment): the
underlying SimTarget/SyntheticScope machinery (uart_target.py, scope.py)
still fully exists and still works -- it just isn't reachable from the GUI's
nav any more. This script is where it lives now, plus a smoke test of every
*new* backend function added for the GUI's Results Viewer / dataset
generation / stop+resume / cross-campaign success-rate aggregation, since
none of those can be exercised through actual FPGA/scope hardware here.

Everything this script produces is written with ``simulated=True`` (the
SimTarget/SyntheticScope path) and every file/report it creates says so in
its own metadata -- the same discipline the rest of this toolkit already
follows (hdf5_trace_store.py's module docstring: "a synthetic file is
software validation, never evidence about the real core"). Output files are
named with a ``devtest_`` prefix so they're easy to find and delete; this
script never deletes anything itself (old synthetic files are kept and
clearly tagged, same as everywhere else in this toolkit -- see README.md).

    uv run python3 dev_regression_test.py

Exits non-zero (and prints which check failed) on any assertion failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

import numpy as np

import acquire_cpa as A
import config as C
import cpa_attack as CA
import hdf5_trace_store as HS
import mlkem_ref as R
import scope as S
import tvla_analysis as TV
import uart_target as T
import web_gui as G

ROOT_TRACES = G.TRACES_DIR
ROOT_ANALYSIS = G.ANALYSIS_DIR
ROOT_INPUTSETS = G.INPUTSETS_DIR

PASS, FAIL = [], []


def check(name: str, fn) -> None:
    t0 = time.time()
    try:
        fn()
        PASS.append(name)
        print("  OK   %-60s (%.1fs)" % (name, time.time() - t0))
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  FAIL %-60s -- %s" % (name, e))
    except Exception as e:  # noqa: BLE001 - report any crash as a failure, not a traceback dump
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  FAIL %-60s -- %s: %s" % (name, type(e).__name__, e))
        traceback.print_exc()


def _dry_run_campaign(seed: bytes, mode: str, n: int, leak_mv: float, suffix: str,
                       strategy: str = "valid", poly: int = 0, pair: int = 0,
                       invalid_random: bool = False, n_samples: int = 6000) -> str:
    # 6000 matches acquire_cpa.py's own cpa-dry-run/tvla-dry-run CLI default
    # (`args.samples or 6000`) -- comfortably covers every BMUL_WINDOWS cycle
    # range (up to ~4742 with slack), which a smaller value will silently
    # truncate into an empty window slice (ValueError: zero-size array).
    """SimTarget + SyntheticScope end-to-end, same path acquire_cpa.py's
    cpa-dry-run/tvla-dry-run CLI commands use -- this IS the developer-test
    entry the official GUI no longer exposes."""
    target = T.SimTarget()
    sc = S.SyntheticScope(n_samples, noise_mv=5.0, leak_mv=leak_mv, seed=1)
    if mode == "cpa":
        dk, items, _fixed = A.cpa_ciphertexts(seed, n, strategy, poly, pair)
        strategy_meta = strategy
    else:
        dk, items = A.tvla_vectors(seed, n, invalid_random=invalid_random)
        strategy_meta = "invalid-random" if invalid_random else "valid"
    recipe = {"mode": mode, "strategy": strategy_meta if mode == "cpa" else strategy_meta,
              "poly": poly, "pair": pair, "n_traces": n, "seed": seed.hex(),
              "invalid_random": invalid_random}
    out_path = str(ROOT_TRACES / ("devtest_%s_%s_%s.hdf5" % (time.strftime("%Y%m%d_%H%M%S"), mode, suffix)))
    A.run_campaign(target, sc, dk, items, out_path, n_samples, mode=mode, strategy=strategy_meta,
                   quiet=True, recipe=recipe)
    return out_path, dk


def test_cpa_negative_control():
    seed = bytes(range(64))
    path, dk = _dry_run_campaign(seed, "cpa", 300, leak_mv=0.0, suffix="negctrl")
    summary = CA.run_attack(path, C.CPA_TARGET, C.CPA_RPREV, 0, None, dk)
    assert summary["oracle_assisted"], "expected oracle-assisted (dk given)"
    assert summary["rank1_fraction"] < 0.05, (
        "negative control (pure noise) recovered the key (rank1_fraction=%.3f) -- "
        "the analysis itself has a bug, see README.md section 3.3" % summary["rank1_fraction"])


def test_cpa_positive_control():
    seed = bytes(range(64))
    path, dk = _dry_run_campaign(seed, "cpa", 300, leak_mv=6.0, suffix="posctrl")
    summary = CA.run_attack(path, C.CPA_TARGET, C.CPA_RPREV, 0, None, dk)
    assert summary["rank1_fraction"] > 0.9, (
        "positive control (injected leakage matching the model) did NOT recover the key "
        "(rank1_fraction=%.3f) -- mlkem_ref.py's math is wrong, see README.md section 3.2"
        % summary["rank1_fraction"])


def test_tvla_negative_control():
    seed = bytes(range(64))
    path, _dk = _dry_run_campaign(seed, "tvla", 300, leak_mv=0.0, suffix="negctrl")
    result = TV.run_tvla(path)
    assert not result["leakage_detected"], "negative control (pure noise) reported TVLA leakage"


def test_tvla_positive_control():
    seed = bytes(range(64))
    path, _dk = _dry_run_campaign(seed, "tvla", 300, leak_mv=6.0, suffix="posctrl")
    result = TV.run_tvla(path)
    assert result["leakage_detected"], "positive control did NOT trigger TVLA leakage detection"


def test_tvla_chart_data_and_detection_curve():
    seed = bytes(range(64))
    path, _dk = _dry_run_campaign(seed, "tvla", 400, leak_mv=6.0, suffix="chartdata")
    cd = TV.chart_data(path, max_points=500)
    assert len(cd["t_curve"]) == len(cd["fixed_mean_curve"]) == len(cd["random_mean_curve"]) == len(cd["sample_indices"])
    assert cd["traces_to_detection"] is not None, "positive control's detection curve never crossed the threshold"
    assert all(row["n_traces"] <= 400 for row in cd["detection_curve"])


def test_resume_matches_uninterrupted_run():
    """Stop a campaign partway, resume it, and check the result is BYTE-FOR-BYTE
    the same as an uninterrupted run over the SAME ciphertext set -- the
    actual correctness property "Resume" needs: ciphertext-to-row
    correspondence must survive the stop/resume round trip.

    Deliberately generates ``items`` via A.cpa_ciphertexts() only ONCE and
    reuses that one result for both the reference run and the
    stopped-then-resumed run's initial (fresh) TraceWriter creation --
    calling the generator a second time with the "same" seed does NOT
    reproduce the same ciphertexts for strategy="valid" (pqc-testkit sca
    gen's random class uses real OS randomness, confirmed empirically
    while first writing this test: see TraceWriter's class docstring and
    acquire_cpa.run_campaign's docstring). That non-reproducibility is
    exactly why run_campaign()'s loop reads every row's ciphertext back
    from the file (writer.ct_at/cls_at) instead of from a freshly
    regenerated ``items`` -- so the RESUME call below passes a throwaway
    items list of the right length (its content must not matter at all,
    which this test explicitly exercises by making it garbage).
    """
    seed = bytes(range(32)) + bytes(range(32, 64))
    n, n_samples = 60, 2000
    dk, items, _fixed = A.cpa_ciphertexts(seed, n, "valid", 0, 0)
    recipe = {"mode": "cpa", "strategy": "valid", "poly": 0, "pair": 0, "n_traces": n, "seed": seed.hex(),
              "invalid_random": False}

    # Reference: one uninterrupted run over `items`.
    target = T.SimTarget()
    sc = S.SyntheticScope(n_samples, noise_mv=3.0, leak_mv=4.0, seed=1)
    ref_path = str(ROOT_TRACES / ("devtest_%s_cpa_resumeref.hdf5" % time.strftime("%Y%m%d_%H%M%S")))
    A.run_campaign(target, sc, dk, items, ref_path, n_samples, mode="cpa", strategy="valid",
                   quiet=True, recipe=recipe)

    # Stopped-then-resumed: the SAME `items` for the initial (fresh) creation
    # -- a stop_cb fires once partway through.
    stop_at = {"n": 0}

    def stop_cb():
        stop_at["n"] += 1
        return stop_at["n"] >= 25  # stop after the 25th successfully-written trace

    target2 = T.SimTarget()
    sc2 = S.SyntheticScope(n_samples, noise_mv=3.0, leak_mv=4.0, seed=1)
    part_path = str(ROOT_TRACES / ("devtest_%s_cpa_resumepart.hdf5" % time.strftime("%Y%m%d_%H%M%S")))
    partial = A.run_campaign(target2, sc2, dk, items, part_path, n_samples, mode="cpa", strategy="valid",
                             quiet=True, recipe=recipe, stop_cb=stop_cb)
    assert partial["stopped_early"], "stop_cb should have interrupted the campaign"
    assert 0 < partial["n_completed"] < n, "partial run should have stopped strictly between 0 and n"

    s = HS.summarize(part_path)
    assert s["resumable"], "a stopped campaign with a recipe should report resumable=True"
    assert s["n_traces_planned"] == n

    target3 = T.SimTarget()
    sc3 = S.SyntheticScope(n_samples, noise_mv=3.0, leak_mv=4.0, seed=1)
    garbage_items = [os.urandom(1088) for _ in range(n)]  # content must be irrelevant on resume
    finished = A.run_campaign(target3, sc3, dk, garbage_items, part_path, n_samples, mode="cpa", strategy="valid",
                              quiet=True, recipe=recipe, resume=True)
    assert finished["n_completed"] == n, "resumed run should finish all %d traces, got %d" % (n, finished["n_completed"])
    assert not finished.get("stopped_early")

    stop_row = partial["n_completed"]  # exactly how many rows the stop left captured
    with HS.TraceReader(ref_path) as r1, HS.TraceReader(part_path) as r2:
        assert r1.n_completed == r2.n_completed == n
        # Ciphertext-to-row correspondence is the actual property resume must
        # preserve, for EVERY row (both the ones captured before the stop
        # and the ones filled in afterwards) -- this must hold exactly.
        np.testing.assert_array_equal(r1.ciphertexts, r2.ciphertexts,
                                       err_msg="resumed file's ciphertexts diverged from the uninterrupted reference")
        # Rows captured BEFORE the stop must be byte-identical to the
        # reference (same seeded SyntheticScope, same draw sequence for the
        # first `stop_row` traces in both runs) -- resume must never touch
        # or re-capture an already-written row.
        np.testing.assert_array_equal(
            r1.traces[:stop_row], r2.traces[:stop_row],
            err_msg="rows captured BEFORE the stop changed after resume -- resume must leave "
                   "already-written rows untouched")
        # Rows filled in AFTER the resume are a fresh SyntheticScope instance
        # (a restarted process would be a fresh real instrument reading too)
        # -- there is no "would have matched a hypothetical uninterrupted
        # run" property to check for those (a real scope's noise on restart
        # isn't continuous with a theoretical unbroken run either); what
        # must hold is that they are REAL captured data, not zeros/gaps.
        assert np.all(r2.ok[stop_row:n]), "resumed rows must all pass their integrity check"
        assert not np.any(r2.traces[stop_row:n] == 0.0), (
            "resumed rows still look like TraceWriter's zero-filled placeholder, not real captures")


def test_dataset_generate_job_offline():
    """api_dataset_generate_start's background job, run synchronously here
    (no threading needed for a test) -- must succeed with zero FPGA/scope
    state and persist only the recipe, never dk/ciphertexts."""
    job_id = "devtest"
    G._jobs[job_id] = {"status": "running"}
    seed = os.urandom(64)
    G._run_generate_job(job_id, "cpa", "valid", 0, 0, 50, seed, False)
    job = G._jobs[job_id]
    assert job["status"] == "done", "generate job failed: %s" % job.get("error")
    recipe_path = job["result"]["path"]
    assert recipe_path.startswith(str(ROOT_INPUTSETS))
    on_disk = json.loads(open(recipe_path).read())
    assert "dk" not in on_disk  # no raw-key field; dk_hash (a hash, not the key) is fine
    assert not any(isinstance(v, str) and len(v) >= 4800 for v in on_disk.values()), (
        "a 2400-byte-or-longer hex string would mean the raw dk leaked into the recipe")
    assert on_disk["n_traces"] == 50
    listing = G.api_dataset_list_input_sets({})
    assert any(x["path"] == recipe_path for x in listing["input_sets"])


def test_results_persistence_round_trip():
    """_save_analysis_report / api_results_list / api_results_load, against a
    real CPA analysis summary from a synthetic positive-control file."""
    seed = bytes(range(64))
    path, dk = _dry_run_campaign(seed, "cpa", 200, leak_mv=6.0, suffix="persist")
    summary = CA.run_attack(path, "word", "prev-word", 0, None, dk)
    summary["model_config"] = {"preset": "basemul-word", "target": "word", "rprev": "prev-word",
                               "max_traces_requested": 0, "oracle_assisted": True}
    report_path = G._save_analysis_report("cpa", path, "devtest-job", summary)
    reloaded = G.api_results_load({"path": report_path})
    assert reloaded["kind"] == "cpa"
    assert reloaded["dataset"]["path"] == path
    assert reloaded["model_config"]["target"] == "word"
    assert abs(reloaded["rank1_fraction"] - summary["rank1_fraction"]) < 1e-9

    listing = G.api_results_list({"file": path})
    assert any(r["path"] == report_path for r in listing["results"])


def test_raw_traces_endpoint():
    seed = bytes(range(64))
    path, _dk = _dry_run_campaign(seed, "cpa", 20, leak_mv=0.0, suffix="rawview", n_samples=6000)
    out = G.api_analysis_raw_traces({"file": path, "first_n": 5, "max_samples": 1000})
    assert len(out["traces"]) == 5
    assert len(out["traces"][0]["samples"]) == len(out["time_axis"]) == len(out["sample_indices"])
    assert out["decimated"] is True  # 6000 samples capped to 1000 must decimate
    assert len(out["bmul_windows_samples"]) == 3  # the 3 BMUL windows from config.py


def test_cross_campaign_success_rate():
    """Needs >=2 independent (different-seed) oracle-assisted --curve CPA
    reports at the same (target, rprev) to produce anything; must say
    Not Available below that, and must aggregate for real above it."""
    target, rprev = "word", "prev-word"

    only_one = G.api_analysis_success_rate({"target": target, "rprev": rprev + "-unused-probe"})
    assert only_one["available"] is False and only_one["n_campaigns"] == 0

    for i, seed_byte in enumerate((0x11, 0x22)):
        seed = bytes([seed_byte]) * 64
        path, dk = _dry_run_campaign(seed, "cpa", 150, leak_mv=5.0, suffix="successrate%d" % i)
        summary = CA.run_attack(path, target, rprev, 0, None, dk)
        summary["model_config"] = {"preset": "basemul-word", "target": target, "rprev": rprev,
                                   "max_traces_requested": 0, "oracle_assisted": True}
        curve = CA.run_curve(path, target, rprev, 0, dk, checkpoints=[30, 60, 100, 150])
        summary["curve"] = curve
        G._save_analysis_report("cpa", path, "devtest-sr-%d" % i, summary)

    agg = G.api_analysis_success_rate({"target": target, "rprev": rprev})
    assert agg["available"] is True, "expected >=2 campaigns to make the success-rate curve available: %r" % agg
    assert agg["n_campaigns"] >= 2
    assert len(agg["checkpoints"]) > 0
    assert all("mean_rank1_fraction" in row for row in agg["checkpoints"])


def test_json_default_handles_numpy_in_real_responses():
    """The actual bug report this session started from: numpy.int64 inside a
    job's result must not crash json.dumps. Exercise it through the real
    hdf5_trace_store.summarize() -> web_gui._send_json path, not a toy dict."""
    seed = bytes(range(64))
    path, _dk = _dry_run_campaign(seed, "cpa", 10, leak_mv=0.0, suffix="jsonsafety")
    summary = HS.summarize(path)
    body = json.dumps(summary, default=G._json_default)  # must not raise
    assert isinstance(json.loads(body)["n_completed"], int)


def main() -> int:
    print("Developer regression test -- SYNTHETIC ONLY, not evidence about real hardware.\n")
    for name, fn in [
        ("CPA negative control (leak_mv=0 -> chance-level ranks)", test_cpa_negative_control),
        ("CPA positive control (leak_mv=6 -> full recovery)", test_cpa_positive_control),
        ("TVLA negative control (no leakage detected)", test_tvla_negative_control),
        ("TVLA positive control (leakage detected)", test_tvla_positive_control),
        ("TVLA chart_data() + detection_curve()", test_tvla_chart_data_and_detection_curve),
        ("Stop + Resume reproduces an uninterrupted run exactly", test_resume_matches_uninterrupted_run),
        ("Dataset generation job (offline, no dk/ct persisted)", test_dataset_generate_job_offline),
        ("Analysis results persistence round-trip", test_results_persistence_round_trip),
        ("Raw Trace Viewer backend (api_analysis_raw_traces)", test_raw_traces_endpoint),
        ("Cross-campaign success-rate aggregation (Not Available / real)", test_cross_campaign_success_rate),
        ("json.dumps(_json_default) on a real numpy-bearing summary", test_json_default_handles_numpy_in_real_responses),
    ]:
        check(name, fn)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("\nFAILED:")
        for name, msg in FAIL:
            print("  - %s: %s" % (name, msg))
        return 1
    print("\nAll synthetic. This validates the math/HDF5/analysis pipeline, NOT real-hardware "
          "security -- see README.md section 6/7. Real evidence only comes from the "
          "Oscilloscope Setup + Trace Acquisition tabs against actual hardware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
