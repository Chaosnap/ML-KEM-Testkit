"""Plaintext-checking (PC) oracle key recovery for the ML-KEM-768 Decaps evaluation.

A published class of attack on FO-transform KEMs (Ravi et al., TCHES 2020; Ueno et al.,
TCHES 2022). It is complementary to the CPA models in ``models/``: instead of correlating a
3329-candidate hypothesis of one NTT-domain coefficient against the BaseMul window, it recovers
one *time-domain* secret coefficient per group of queries with a single binary measurement, read
from the loud part of the trace (the FO re-encryption, where a fixed-vs-random TVLA already shows
leakage, see ``config.DECAPS_PHASES``).

Mechanism. Decaps accepts arbitrary bytes as a ciphertext. With ``u`` equal to the constant
polynomial ``k`` in one slot and zero elsewhere, ``INTT(s_hat . u_hat) = k * s_poly``, so the
decrypted message is ``m'[t] = Compress_1( v[t] - k*s_poly[t] )``. Choosing ``v`` zero except at
one position ``t``, and ``k = Decompress_10(64) ~= q/16``, forces ``m'`` to be either the zero
message or weight one; which it is depends only on ``s_poly[t]``. The ML-KEM-768 secret is
sampled from a centred binomial (eta=2, values in ``{-2..2}``), so sweeping ``v[t]`` over four
levels gives a thermometer code of that coefficient (:func:`plan`). ``m'`` seeds ``G(m'||h)`` and
the whole re-encryption, so the two outcomes differ across most of the trace.

Labelling needs no key: ``z=0`` forces ``m'=0`` and ``z=7`` forces weight one for every possible
secret (:func:`reference_plan`), so the two power templates are measured on the same device and a
coefficient's queries are classified against them (:func:`classify`). The chosen ciphertexts are
not valid encapsulations; the core's implicit-rejection path still returns a key, which is
discarded -- the measurement is of the message-dependent power, not of that key.

This module builds the query plan and decodes measured labels back to the key; it does not drive
the scope (that is ``acquire_cpa`` / the GUI). ``verify_recovery`` checks a full plan against a
known key in software, with no hardware.
"""

from __future__ import annotations

import numpy as np

import mlkem_ref as R

Q = R.Q
#: ML-KEM-768 secret coefficients: centred binomial eta=2, i.e. integers in [-2, 2].
S_MIN, S_MAX = -2, 2
SECRET_VALUES = tuple(range(S_MIN, S_MAX + 1))
#: u multiplier: Decompress_10(U_CODE) ~= q/16, small enough that the 255 untargeted message
#: positions round to 0 for every possible key (so m' is zero or weight one).
U_CODE = 64


def decompress(code: int, d: int) -> int:
    """Decompress_d of one coefficient (FIPS 203), the value the core reconstructs."""
    return (code * Q + (1 << (d - 1))) >> d


def message_bit(v_code: int, s_value: int) -> int:
    """Compress_1( Decompress_4(v_code) - k*s ) at the attacked position, k = Decompress_10(U_CODE)."""
    k = decompress(U_CODE, 10)
    w = (decompress(v_code, 4) - k * s_value) % Q
    return int(R.compress(np.array([w]), 1)[0])


def _thermometer_levels() -> list[int]:
    """The v-codes whose message bit steps once as the secret goes -2 -> 2: one per boundary.

    The code picks, for each adjacent pair of secret values, a v where exactly those two differ,
    so the four queries' bits are a monotone (thermometer) encoding of the five-valued secret.
    """
    levels = []
    for lo, hi in zip(SECRET_VALUES, SECRET_VALUES[1:]):
        chosen = None
        for z in range(1, 16):
            bits = [message_bit(z, s) for s in SECRET_VALUES]
            # a clean boundary at (lo, hi): 1 for every secret <= lo, 0 for every secret >= hi
            if all(bits[i] == (1 if s <= lo else 0) for i, s in enumerate(SECRET_VALUES)):
                chosen = z
                break
        if chosen is None:
            raise RuntimeError("no clean v-code for the %d/%d boundary" % (lo, hi))
        levels.append(chosen)
    return levels


#: One v-code per secret boundary (-2|-1, -1|0, 0|1, 1|2): the thermometer sweep.
THERMO_LEVELS = _thermometer_levels()
#: Reference v-codes, both with a nonzero v at the attacked position so the ciphertext still names
#: the coefficient (unlike v=0): REF_ZERO forces m' = 0 and REF_ONE forces m' weight-one, for every
#: possible secret. Checked key-independent in :func:`_reference_codes` at import.
REF_ZERO, REF_ONE = 1, 7


def _reference_codes() -> tuple[int, int]:
    """Assert the two reference codes give a key-independent message bit (0 resp. 1 for every secret)."""
    assert {message_bit(REF_ZERO, s) for s in SECRET_VALUES} == {0}, "REF_ZERO not key-independent"
    assert {message_bit(REF_ONE, s) for s in SECRET_VALUES} == {1}, "REF_ONE not key-independent"
    return REF_ZERO, REF_ONE


_reference_codes()


def chosen_ciphertext(poly: int, t: int, v_code: int) -> bytes:
    """A ciphertext isolating coefficient ``t`` of ``s[poly]``: u = U_CODE in slot ``poly``, v = ``v_code`` at ``t``.

    Not a valid encapsulation (no ek, no m). ``u`` is placed as a *compressed* coefficient so the
    core's Decompress_10 reconstructs exactly U_CODE -- no NTT, so no rounding is amplified (unlike
    ``mlkem_ref.structured_ciphertext``, which compresses a time-domain polynomial and loses the
    isolation). ``v`` likewise is one compressed coefficient the core's Decompress_4 reconstructs.
    """
    if not 0 <= poly < R.K:
        raise ValueError("poly must be 0..%d" % (R.K - 1))
    if not 0 <= t < 256:
        raise ValueError("coeff index t must be 0..255")
    cu = np.zeros((R.K, 256), dtype=np.int64)
    cu[poly, 0] = U_CODE                       # u_hat_from_ct decodes c1 then NTTs: constant poly -> all-NTT = U_CODE
    cv = np.zeros(256, dtype=np.int64)
    cv[t] = int(v_code)
    return R.byte_encode(cu, R.DU).tobytes() + R.byte_encode(cv, R.DV).tobytes()


def parse_ciphertext(ct: bytes) -> dict | None:
    """Recognise a chosen ciphertext from its bytes: ``{poly, coeff, v_code, kind, level}`` or None.

    A capture's query is recovered from the stored ciphertext itself, so a PC-oracle campaign needs
    no extra per-trace labels: it is an ordinary CPA campaign whose ciphertexts happen to be these.
    ``kind`` is ``"sweep"`` (then ``level`` is 0..3), ``"ref-zero"`` or ``"ref-one"``; None if the bytes
    are not one of ours (a wrong u pattern, or more than one nonzero v).
    """
    b = np.frombuffer(ct, dtype=np.uint8)
    if b.shape[0] != R.CT_LEN:
        return None
    cu = R.byte_decode(b[:R.C1_LEN].reshape(R.K, 32 * R.DU), R.DU)   # (K, 256), per polynomial
    cv = R.byte_decode(b[R.C1_LEN:], R.DV)
    u_nz = [(p, j) for p in range(R.K) for j in np.nonzero(cu[p])[0]]
    v_nz = list(np.nonzero(cv)[0])
    if len(u_nz) != 1 or u_nz[0][1] != 0 or int(cu[u_nz[0]]) != U_CODE or len(v_nz) != 1:
        return None
    poly, t, z = u_nz[0][0], int(v_nz[0]), int(cv[v_nz[0]])
    out = {"poly": int(poly), "coeff": t, "v_code": z}
    if z in THERMO_LEVELS:
        out.update(kind="sweep", level=THERMO_LEVELS.index(z))
    elif z == REF_ZERO:
        out.update(kind="ref-zero", level=None)
    elif z == REF_ONE:
        out.update(kind="ref-one", level=None)
    else:
        return None
    return out


def plan(polys=range(R.K), coeffs=range(256)) -> list[dict]:
    """The sweep queries: for every coefficient, four ciphertexts (one per thermometer level).

    Each item is ``{poly, coeff, level, v_code, ct}``; ``level`` is 0..3 (the boundary index).
    """
    out = []
    for poly in polys:
        for t in coeffs:
            for level, z in enumerate(THERMO_LEVELS):
                out.append({"poly": poly, "coeff": int(t), "level": level, "v_code": z,
                            "ct": chosen_ciphertext(poly, t, z)})
    return out


def reference_plan(polys=range(R.K), coeffs=range(256)) -> list[dict]:
    """The two key-independent reference queries per coefficient: ``m'`` forced to 0 and to weight one.

    Captured on the same device, these are the templates :func:`classify` matches the sweep against,
    so the whole recovery needs no prior knowledge of the key.
    """
    out = []
    for poly in polys:
        for t in coeffs:
            out.append({"poly": poly, "coeff": int(t), "ref": "zero", "v_code": REF_ZERO,
                        "ct": chosen_ciphertext(poly, t, REF_ZERO)})
            out.append({"poly": poly, "coeff": int(t), "ref": "one", "v_code": REF_ONE,
                        "ct": chosen_ciphertext(poly, t, REF_ONE)})
    return out


def _thermometer(s: int) -> list[int]:
    """The ideal (noiseless) thermometer code of secret ``s``: ``THERMO_LEVELS[i]`` fires once ``s`` drops
    to or below its boundary, so the code is non-decreasing with ``S_MAX - s`` ones at the top end."""
    ones = S_MAX - s
    return [0] * (len(THERMO_LEVELS) - ones) + [1] * ones


def decode_bits(bits) -> int:
    """A thermometer code back to the secret in [-2, 2]: ``s = S_MAX - popcount`` (order-independent)."""
    return S_MAX - int(np.sum(np.asarray(bits) != 0))


def _monotone(bits) -> bool:
    b = [int(x) for x in bits]
    return b == sorted(b)                         # the code is non-decreasing: 0..0 1..1


def decode_coefficient(bits) -> dict:
    """Decode one coefficient's four measured bits: ``{value, clean, bits}``.

    ``clean`` is False when the pattern is not a clean thermometer (then ``value`` is the nearest one
    by Hamming distance); a run with many non-clean coefficients needs more traces per query.
    """
    bits = [int(x) for x in bits]
    if _monotone(bits):
        return {"value": decode_bits(bits), "clean": True, "bits": bits}
    best, best_d = S_MAX, len(bits) + 1
    for s in SECRET_VALUES:
        d = sum(a != b for a, b in zip(_thermometer(s), bits))
        if d < best_d:
            best, best_d = s, d
    return {"value": best, "clean": False, "bits": bits}


def classify(sweep_feature: np.ndarray, zero_feature: np.ndarray, one_feature: np.ndarray) -> np.ndarray:
    """Label each sweep query 0/1 by which reference its trace feature is closer to.

    ``*_feature`` are one scalar per query (e.g. the mean of ``cycle_features`` over the
    re-encryption window, or a template projection); shapes broadcast over the query axis. Returns
    a 0/1 array the size of ``sweep_feature`` (1 = closer to the weight-one reference).
    """
    sweep = np.asarray(sweep_feature, dtype=np.float64)
    midpoint = (np.asarray(zero_feature, dtype=np.float64) + np.asarray(one_feature, dtype=np.float64)) / 2.0
    polarity = np.sign(np.asarray(one_feature, dtype=np.float64) - np.asarray(zero_feature, dtype=np.float64))
    return ((sweep - midpoint) * polarity > 0).astype(np.uint8)


def secret_time_domain(dk: bytes) -> np.ndarray:
    """The secret in the time domain, centred to [-2, 2]: ``(K, 256)``. For checking a recovery."""
    s = R.intt(R.s_hat_from_dk(dk))
    return np.where(s > Q // 2, s - Q, s).astype(np.int64)


def ideal_bits(dk: bytes, poly: int, t: int) -> list[int]:
    """The four thermometer bits a noiseless oracle would return for ``s[poly][t]`` (for tests/templates)."""
    s = int(secret_time_domain(dk)[poly, t])
    return [message_bit(z, s) for z in THERMO_LEVELS]


def verify_recovery(dk: bytes, polys=range(R.K), coeffs=range(256)) -> dict:
    """Software end-to-end check (no hardware): decode the ideal labels of every planned query and
    compare with the real key. Proves the plan and the decoder recover the whole secret."""
    truth = secret_time_domain(dk)
    wrong = []
    for poly in polys:
        for t in coeffs:
            got = decode_coefficient(ideal_bits(dk, poly, t))["value"]
            if got != int(truth[poly, t]):
                wrong.append((poly, int(t), got, int(truth[poly, t])))
    n = len(list(polys)) * len(list(coeffs))
    return {"coefficients": n, "recovered": n - len(wrong), "wrong": wrong[:16], "ok": not wrong,
            "queries_per_coefficient": len(THERMO_LEVELS), "total_queries": n * len(THERMO_LEVELS)}


# =============================================================================
# Decoding a captured campaign
# =============================================================================
def reencrypt_window(rate_hz: float, n_samples: int) -> tuple[int, int]:
    """Sample range of the message-dependent part of the trace (G(m'||h) to the end of the record).

    The decrypted message m' is first used at the start of ``G(m'||h)`` (``config.DECAPS_PHASES``);
    everything after depends on it. Clipped to what the record actually holds.
    """
    import config as C
    sr = rate_hz if rate_hz > 0 else C.CORE_MHZ * 1e6
    phases = {name: (a, b) for name, a, b in getattr(C, "DECAPS_PHASES", [])}
    start_cyc = phases.get("G(m′‖h)", (6519, 0))[0]
    s0 = int(round(start_cyc / C.CORE_MHZ / 1e6 * sr))
    return min(n_samples, max(0, s0)), n_samples


def trace_feature(traces: np.ndarray, rate_hz: float, window: tuple[int, int] | None = None) -> np.ndarray:
    """One scalar per trace: the mean absolute deviation over the message-dependent window.

    A deliberately simple, polarity-free statistic (the message changes the amount of switching over
    a long window, not one sample's sign), so it needs no template and no clock alignment. ``window``
    overrides the default re-encryption window.
    """
    t = np.asarray(traces, dtype=np.float64)
    a, b = window or reencrypt_window(rate_hz, t.shape[1])
    seg = t[:, a:b]
    return np.abs(seg - seg.mean(axis=1, keepdims=True)).mean(axis=1)


def decode_campaign(h5_path: str, feature=None, max_traces: int = 0) -> dict:
    """Recover the key from a captured PC-oracle campaign (an ordinary CPA HDF5 of chosen ciphertexts).

    Each trace's query is recognised from its stored ciphertext (:func:`parse_ciphertext`); traces of
    the same query are averaged, the two references give a per-coefficient 0/1 threshold, each sweep
    query is labelled against it, and the thermometer is decoded. ``feature(traces, rate)`` returns one
    scalar per trace (default :func:`trace_feature`); pass a template projection for a stronger result.

    Returns the recovered secret ``(K, 256)`` (time domain, [-2, 2]), per-coefficient confidence and
    cleanliness, and -- when the file's ``recipe`` lets dk be rebuilt -- the fraction correct.
    """
    from hdf5_trace_store import TraceReader
    feature = feature or (lambda tr, rate: trace_feature(tr, rate))
    with TraceReader(h5_path) as r:
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces = r.traces[:n][ok]
        cts = r.ciphertexts[:n][ok]
        rate = r.sample_rate
        meta = r.metadata
    if traces.shape[0] == 0:
        raise SystemExit("no usable traces in %s" % h5_path)
    feats = feature(traces, rate)

    # bucket trace features by query
    sweep: dict = {}      # (poly, coeff) -> {level: [features]}
    ref: dict = {}        # (poly, coeff) -> {"zero": [...], "one": [...]}
    unknown = 0
    for i in range(cts.shape[0]):
        q = parse_ciphertext(cts[i].tobytes())
        if q is None:
            unknown += 1
            continue
        key = (q["poly"], q["coeff"])
        if q["kind"] == "sweep":
            sweep.setdefault(key, {}).setdefault(q["level"], []).append(feats[i])
        else:
            ref.setdefault(key, {"zero": [], "one": []})[q["kind"].split("-")[1]].append(feats[i])

    secret = np.full((R.K, 256), 99, dtype=np.int64)
    coeffs, confidences = [], []
    for key, levels in sorted(sweep.items()):
        poly, t = key
        refs = ref.get(key)
        if not refs or not refs["zero"] or not refs["one"] or len(levels) < len(THERMO_LEVELS):
            continue
        zero_m, one_m = np.mean(refs["zero"]), np.mean(refs["one"])
        mid, pol = (zero_m + one_m) / 2.0, np.sign(one_m - zero_m)
        bits = [int((np.mean(levels[l]) - mid) * pol > 0) for l in range(len(THERMO_LEVELS))]
        d = decode_coefficient(bits)
        secret[poly, t] = d["value"]
        coeffs.append((poly, t, d["value"], d["clean"]))
        confidences.append(abs(one_m - zero_m))

    out = {"file": h5_path, "n_traces_used": int(traces.shape[0]), "unknown_ciphertexts": unknown,
           "coefficients_recovered": len(coeffs), "coefficients_total": R.K * 256,
           "coefficients_clean": sum(1 for *_, c in coeffs if c),
           "secret_time_domain": secret.tolist(),
           "median_confidence": float(np.median(confidences)) if confidences else 0.0}
    recipe = meta.get("recipe")
    if recipe:
        import json
        import acquire_cpa as A
        try:
            dk = A.dk_from_recipe(json.loads(str(recipe)))
            truth = secret_time_domain(dk)
            got = [(p, t, v) for p, t, v, _ in coeffs]
            right = sum(1 for p, t, v in got if v == int(truth[p, t]))
            out["known_key"] = True
            out["correct"] = right
            out["correct_fraction"] = right / len(got) if got else 0.0
        except Exception:  # noqa: BLE001
            out["known_key"] = False
    return out


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Plaintext-checking oracle key recovery (see the module docstring).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="software self-check: recover a key from the ideal oracle, no hardware")
    v.add_argument("--seed", default="00" * 64, help="hex 64-byte KeyGen seed")
    d = sub.add_parser("decode", help="recover the key from a captured PC-oracle campaign (HDF5)")
    d.add_argument("file")
    d.add_argument("--max-traces", type=int, default=0)
    p = sub.add_parser("plan", help="print the query plan size for the whole key")
    args = ap.parse_args()

    if args.cmd == "verify":
        import mlkem768 as KEM
        s64 = seed64 = __import__("testvectors").seed64(bytes.fromhex(args.seed))
        dk = KEM.keygen(s64[:32], s64[32:])[1]
        res = verify_recovery(dk)
        print("software recovery: %s  (%d/%d coefficients, %d queries)"
              % ("OK" if res["ok"] else "FAIL", res["recovered"], res["coefficients"], res["total_queries"]))
        if not res["ok"]:
            print("  wrong (first few):", res["wrong"])
            raise SystemExit(1)
    elif args.cmd == "plan":
        n = R.K * 256
        print("whole key: %d coefficients" % n)
        print("  sweep queries:     %d (%d per coefficient)" % (n * len(THERMO_LEVELS), len(THERMO_LEVELS)))
        print("  reference queries: %d (2 per coefficient, key-independent)" % (n * 2))
        print("  each captured R times and averaged; R sets the trace budget (see docs/ATTACK_MODEL.md)")
    else:
        res = decode_campaign(args.file, max_traces=args.max_traces)
        print("file:                 %s" % res["file"])
        print("traces used:          %d  (unknown ciphertexts: %d)" % (res["n_traces_used"], res["unknown_ciphertexts"]))
        print("coefficients recovered: %d / %d  (%d clean)"
              % (res["coefficients_recovered"], res["coefficients_total"], res["coefficients_clean"]))
        print("median class separation: %.4f" % res["median_confidence"])
        if res.get("known_key"):
            print("CORRECT (oracle-assisted check): %d / %d = %.1f%% of the recovered coefficients"
                  % (res["correct"], res["coefficients_recovered"], 100 * res["correct_fraction"]))


if __name__ == "__main__":
    _main()
