"""Test vectors for the ML-KEM-768 Decaps core: generate, store, export.

A *vector set* is everything one acquisition campaign sends to the board,
fixed before any trace is captured:

- one key pair ``(ek, dk)`` from ``KeyGen_internal(d, z)``, ``d||z`` = the seed;
- N ciphertexts, each with its class label (TVLA: 0 = fixed, 1 = random),
  the message it encapsulates (when there is one) and the shared secret
  ``K`` the board must return -- computed here with ``mlkem768.py``, so every
  single trace of a campaign can be checked against the correct answer.

Everything is a deterministic function of the seed: the same parameters and
seed always give byte-identical vectors (the random class, invalid
ciphertexts and shuffle order come from a PCG64 generator seeded with
SHA3-256 of the seed). For a 64-byte seed, ``dk`` and the TVLA fixed
ciphertext are also identical to what the earlier (since removed) Go
generator ``pqc-testkit sca gen`` produced for that seed.

Kinds (``mode`` / ``strategy``):

- CPA ``valid``      fresh FIPS 203 encapsulations of random messages
- CPA ``invalid``    uniformly random bytes as ciphertext (implicit rejection)
- CPA ``structured`` ``mlkem_ref.structured_ciphertext``: u_hat zero except one
                     BaseMul pair (isolates one pair for the leakage model)
- TVLA               half fixed (one ciphertext, repeated), half random
                     (valid encapsulations, or random bytes with
                     ``invalid_random``), shuffled

Stored as one ``.npz`` per set under ``data/vectors/`` (``dk`` included:
these are test keys, generated for the evaluation, not secrets).

    python testvectors.py gen --mode cpa -n 5000 [--strategy valid] [--seed HEX]
    python testvectors.py gen --mode tvla -n 5000 [--invalid-random]
    python testvectors.py list
    python testvectors.py export data/vectors/<set>.npz --format csv -o vectors.csv
    python testvectors.py verify data/vectors/<set>.npz
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import mlkem768 as KEM
import mlkem_ref as R

ROOT = Path(__file__).resolve().parents[1]
VECTORS_DIR = ROOT / "data" / "vectors"
GENERATOR = "cpa_toolkit.testvectors/1 (FIPS 203, deterministic from seed)"
STRATEGIES = {
    "valid": "fresh FIPS 203 encapsulations of random messages (accept path)",
    "invalid": "uniformly random bytes as ciphertext (implicit-rejection path)",
    "structured": "u_hat zero except one BaseMul pair (needs poly 0-2, pair 0-127)",
}
_CHUNK = 256  # ciphertexts per vectorised batch (progress granularity)


def seed64(seed: bytes) -> bytes:
    """The KeyGen seed d||z: the seed itself if it is 64 bytes, else SHAKE256 of it."""
    if len(seed) == 64:
        return seed
    return hashlib.shake_256(b"ml-kem-testkit seed" + seed).digest(64)


def _rng(seed: bytes) -> np.random.Generator:
    digest = hashlib.sha3_256(b"ml-kem-testkit rng" + seed64(seed)).digest()
    return np.random.Generator(np.random.PCG64(int.from_bytes(digest, "little")))


@dataclass
class VectorSet:
    meta: dict
    dk: bytes
    ek: bytes
    cls: np.ndarray     # (N,) uint8   0 = fixed, 1 = random (TVLA); 1 for every CPA row
    ct: np.ndarray      # (N, 1088) uint8
    m: np.ndarray       # (N, 32) uint8, zero where has_m is False
    has_m: np.ndarray   # (N,) bool    ciphertext is an encapsulation of m
    k: np.ndarray       # (N, 32) uint8 expected shared secret (what Decaps returns)
    valid: np.ndarray   # (N,) bool    passes the FO re-encryption check

    @property
    def n(self) -> int:
        return int(self.ct.shape[0])

    @property
    def mode(self) -> str:
        return self.meta["mode"]

    def campaign_items(self) -> list:
        """What acquire_cpa.run_campaign takes: ``[ct]`` (CPA) or ``[(cls, ct)]`` (TVLA)."""
        cts = [c.tobytes() for c in self.ct]
        if self.mode == "tvla":
            return [(int(c), ct) for c, ct in zip(self.cls, cts)]
        return cts

    def row(self, i: int) -> dict:
        return {"index": i, "class": int(self.cls[i]), "ct": self.ct[i].tobytes().hex(),
                "m": self.m[i].tobytes().hex() if self.has_m[i] else "",
                "k": self.k[i].tobytes().hex(), "valid": bool(self.valid[i])}

    # --- persistence -------------------------------------------------------------
    def save(self, path: Path | None = None) -> Path:
        if path is None:
            VECTORS_DIR.mkdir(parents=True, exist_ok=True)
            path = VECTORS_DIR / (self.meta["id"] + ".npz")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, meta=np.array(json.dumps(self.meta)),
                 dk=np.frombuffer(self.dk, dtype=np.uint8), ek=np.frombuffer(self.ek, dtype=np.uint8),
                 cls=self.cls, ct=self.ct, m=self.m, has_m=self.has_m, k=self.k, valid=self.valid)
        return path

    @classmethod
    def load(cls, path: Path | str) -> "VectorSet":
        with np.load(path, allow_pickle=False) as z:
            return cls(meta=json.loads(str(z["meta"])), dk=z["dk"].tobytes(), ek=z["ek"].tobytes(),
                       cls=z["cls"], ct=z["ct"], m=z["m"], has_m=z["has_m"], k=z["k"], valid=z["valid"])

    # --- export ------------------------------------------------------------------
    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["index", "class", "ciphertext_hex", "message_hex", "expected_k_hex", "valid"])
        for i in range(self.n):
            r = self.row(i)
            w.writerow([r["index"], r["class"], r["ct"], r["m"], r["k"], int(r["valid"])])
        return buf.getvalue()

    def to_json(self) -> str:
        return json.dumps({"meta": self.meta, "dk": self.dk.hex(), "ek": self.ek.hex(),
                           "vectors": [self.row(i) for i in range(self.n)]}, indent=1)

    def verify_software(self, sample: int = 64) -> dict:
        """Re-run Decaps in software on (up to) ``sample`` rows and compare with the stored K."""
        idx = np.unique(np.linspace(0, self.n - 1, num=min(sample, self.n)).astype(int))
        keys, valid = KEM.decaps_batch(self.dk, self.ct[idx])
        bad = [int(i) for j, i in enumerate(idx) if keys[j].tobytes() != self.k[i].tobytes()
               or bool(valid[j]) != bool(self.valid[i])]
        return {"checked": int(len(idx)), "mismatches": bad, "ok": not bad}


def _expected_keys(dk: bytes, ct: np.ndarray, a_hat, progress) -> tuple[np.ndarray, np.ndarray]:
    keys = np.empty((ct.shape[0], 32), dtype=np.uint8)
    valid = np.empty(ct.shape[0], dtype=bool)
    for s in range(0, ct.shape[0], _CHUNK):
        keys[s:s + _CHUNK], valid[s:s + _CHUNK] = KEM.decaps_batch(dk, ct[s:s + _CHUNK], a_hat)
        progress(min(s + _CHUNK, ct.shape[0]))
    return keys, valid


def _encapsulate(ek: bytes, ms: list[bytes], a_hat, progress) -> tuple[np.ndarray, np.ndarray]:
    keys = np.empty((len(ms), 32), dtype=np.uint8)
    cts = np.empty((len(ms), R.CT_LEN), dtype=np.uint8)
    for s in range(0, len(ms), _CHUNK):
        keys[s:s + _CHUNK], cts[s:s + _CHUNK] = KEM.encaps_batch(ek, ms[s:s + _CHUNK], a_hat)
        progress(min(s + _CHUNK, len(ms)))
    return keys, cts


def generate(mode: str, n: int, seed: bytes, strategy: str = "valid", poly: int = 0, pair: int = 0,
             invalid_random: bool = False, progress_cb=None) -> VectorSet:
    """Build a vector set. ``progress_cb(done, total, message)`` is optional."""
    if mode not in ("cpa", "tvla"):
        raise ValueError("mode must be 'cpa' or 'tvla'")
    if mode == "cpa" and strategy not in STRATEGIES:
        raise ValueError("strategy must be one of %s" % ", ".join(STRATEGIES))
    if n < (2 if mode == "tvla" else 1):
        raise ValueError("n must be >= %d" % (2 if mode == "tvla" else 1))
    if not 0 <= poly < R.K or not 0 <= pair < 128:
        raise ValueError("poly must be 0..2 and pair 0..127")
    report = progress_cb or (lambda done, total, msg: None)
    s64 = seed64(seed)
    rng = _rng(seed)

    report(0, n, "KeyGen")
    ek, dk = KEM.keygen(s64[:32], s64[32:])
    a_hat = KEM.matrix_a(ek[KEM.EK_PKE_LEN:])
    m = np.zeros((n, 32), dtype=np.uint8)
    has_m = np.zeros(n, dtype=bool)
    cls = np.ones(n, dtype=np.uint8)

    if mode == "cpa" and strategy == "valid":
        msgs = [rng.bytes(32) for _ in range(n)]
        k, ct = _encapsulate(ek, msgs, a_hat, lambda d: report(d, n, "encapsulating"))
        m[:] = np.frombuffer(b"".join(msgs), dtype=np.uint8).reshape(n, 32)
        has_m[:] = True
        valid = np.ones(n, dtype=bool)
    elif mode == "cpa":
        if strategy == "invalid":
            ct = rng.integers(0, 256, size=(n, R.CT_LEN), dtype=np.uint8)
        else:
            ct = np.stack([np.frombuffer(R.structured_ciphertext(poly, pair, rng), dtype=np.uint8)
                           for _ in range(n)])
        k, valid = _expected_keys(dk, ct, a_hat, lambda d: report(d, n, "computing expected K"))
    else:
        n_fixed = n // 2
        n_rand = n - n_fixed
        m0 = R.fixed_message(s64)
        k0, ct0 = KEM.encaps_batch(ek, [m0], a_hat)
        if invalid_random:
            ct_r = rng.integers(0, 256, size=(n_rand, R.CT_LEN), dtype=np.uint8)
            k_r, valid_r = _expected_keys(dk, ct_r, a_hat, lambda d: report(d, n, "computing expected K"))
            m_r, has_r = np.zeros((n_rand, 32), dtype=np.uint8), np.zeros(n_rand, dtype=bool)
        else:
            msgs = [rng.bytes(32) for _ in range(n_rand)]
            k_r, ct_r = _encapsulate(ek, msgs, a_hat, lambda d: report(d, n, "encapsulating"))
            valid_r = np.ones(n_rand, dtype=bool)
            m_r, has_r = np.frombuffer(b"".join(msgs), dtype=np.uint8).reshape(n_rand, 32), np.ones(n_rand, dtype=bool)
        ct = np.concatenate([np.repeat(ct0, n_fixed, axis=0), ct_r])
        k = np.concatenate([np.repeat(k0, n_fixed, axis=0), k_r])
        valid = np.concatenate([np.ones(n_fixed, dtype=bool), valid_r])
        m = np.concatenate([np.repeat(np.frombuffer(m0, dtype=np.uint8)[None], n_fixed, axis=0), m_r])
        has_m = np.concatenate([np.ones(n_fixed, dtype=bool), has_r])
        cls = np.concatenate([np.zeros(n_fixed, dtype=np.uint8), np.ones(n_rand, dtype=np.uint8)])
        order = rng.permutation(n)  # interleave the classes against slow drift of the setup
        ct, k, valid, m, has_m, cls = ct[order], k[order], valid[order], m[order], has_m[order], cls[order]

    label = strategy if mode == "cpa" else ("invalid-random" if invalid_random else "valid-random")
    created = time.time()
    meta = {
        "id": "%s_%s_%s_n%d" % (time.strftime("%Y%m%d_%H%M%S", time.localtime(created)), mode,
                                label, n),
        "mode": mode, "strategy": strategy if mode == "cpa" else label,
        "poly": poly if strategy == "structured" else None,
        "pair": pair if strategy == "structured" else None,
        "invalid_random": bool(invalid_random) if mode == "tvla" else None,
        "n": n, "seed": seed.hex(), "created_unix": created, "generator": GENERATOR,
        "dk_sha3_256": hashlib.sha3_256(dk).hexdigest(),
        "n_fixed": int((cls == 0).sum()), "n_random": int((cls == 1).sum()),
        "n_valid": int(valid.sum()),
    }
    report(n, n, "done")
    return VectorSet(meta, dk, ek, cls, ct.astype(np.uint8), m, has_m, k, valid)


def annotations_path(npz: Path) -> Path:
    """The side file next to ``<set>.npz`` that holds its user name and tags
    (kept outside the .npz so editing them never rewrites the vectors)."""
    npz = Path(npz)
    return npz.with_name(npz.stem + ".meta.json")


def read_annotations(npz: Path) -> dict:
    """``{"label": str, "tags": [str]}`` of a stored set (empty when never annotated)."""
    try:
        a = json.loads(annotations_path(npz).read_text())
    except (OSError, ValueError):
        a = {}
    return {"label": str(a.get("label", "") or ""), "tags": [str(t) for t in a.get("tags", []) or []]}


def write_annotations(npz: Path, label: str | None = None, tags: list[str] | None = None) -> dict:
    """Update the name and/or tags of a stored set; returns the new annotations."""
    a = read_annotations(npz)
    if label is not None:
        a["label"] = label
    if tags is not None:
        a["tags"] = list(tags)
    annotations_path(npz).write_text(json.dumps(a, indent=1))
    return a


def list_sets(directory: Path = VECTORS_DIR) -> list[dict]:
    """Metadata of every stored set, newest first (reads only the small ``meta``
    member), with its user name and tags."""
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(directory.glob("*.npz"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with np.load(p, allow_pickle=False) as z:
                meta = json.loads(str(z["meta"]))
        except Exception:  # noqa: BLE001 - skip unreadable/partial files
            continue
        out.append(dict(meta, path=str(p), filename=p.name, size_bytes=p.stat().st_size, **read_annotations(p)))
    return out


def resolve(path: str, directory: Path = VECTORS_DIR) -> Path:
    """A stored set's path, refusing anything outside ``directory``."""
    p = Path(path)
    if not p.is_absolute():
        p = directory / p
    if directory.resolve() not in p.resolve().parents:
        raise ValueError("refusing to read outside %s" % directory)
    if not p.is_file():
        raise ValueError("no such vector set: %s" % p.name)
    return p


def main() -> None:
    import os
    import sys
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate and store a vector set")
    g.add_argument("--mode", choices=["cpa", "tvla"], default="cpa")
    g.add_argument("-n", type=int, default=1000)
    g.add_argument("--strategy", choices=list(STRATEGIES), default="valid")
    g.add_argument("--poly", type=int, default=0)
    g.add_argument("--pair", type=int, default=0)
    g.add_argument("--invalid-random", action="store_true")
    g.add_argument("--seed", default="", help="hex; random if empty")
    sub.add_parser("list", help="list stored vector sets")
    e = sub.add_parser("export", help="export a stored set as csv / json / dk / ek")
    e.add_argument("path")
    e.add_argument("--format", choices=["csv", "json", "dk", "ek"], default="csv")
    e.add_argument("-o", "--output", default="-")
    v = sub.add_parser("verify", help="re-check every expected K with software Decaps")
    v.add_argument("path")
    a = ap.parse_args()

    if a.cmd == "gen":
        seed = bytes.fromhex(a.seed) if a.seed else os.urandom(64)
        vs = generate(a.mode, a.n, seed, a.strategy, a.poly, a.pair, a.invalid_random,
                      lambda d, t, m: print("\r%s %d/%d   " % (m, d, t), end="", file=sys.stderr))
        print(file=sys.stderr)
        print(vs.save())
    elif a.cmd == "list":
        for m in list_sets():
            print("%-48s %-5s %-15s n=%-6d seed=%s...%s%s" % (
                m["filename"], m["mode"], m["strategy"], m["n"], m["seed"][:16],
                "  \"%s\"" % m["label"] if m["label"] else "", "".join("  #" + t for t in m["tags"])))
    elif a.cmd == "export":
        vs = VectorSet.load(a.path)
        if a.format in ("dk", "ek"):
            data = vs.dk if a.format == "dk" else vs.ek
            if a.output == "-":
                print(data.hex())
            else:
                Path(a.output).write_bytes(data)
            return
        text = vs.to_csv() if a.format == "csv" else vs.to_json()
        if a.output == "-":
            sys.stdout.write(text)
        else:
            Path(a.output).write_text(text)
    else:
        vs = VectorSet.load(a.path)
        r = vs.verify_software(sample=vs.n)
        print("%s: %d rows checked, %s" % (a.path, r["checked"],
                                           "all expected K correct" if r["ok"] else "MISMATCH %s" % r["mismatches"]))
        sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
