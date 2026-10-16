"""ML-KEM-768 (FIPS 203) in Python: KeyGen, Encaps, Decaps.

The host-side reference the test-vector generator (``testvectors.py``) is
built on: the exact algorithms the hardware core implements, so every
vector carries the shared secret the board must return. Checked byte for
byte against the NIST ACVP vectors in ``tests/acvp/`` (``tests/test_mlkem768.py``).

The transforms and encodings are the ones of ``mlkem_ref.py`` (the leakage
model uses the same code, so the two can never disagree). Encaps and
Decaps are vectorised over a batch of messages / ciphertexts: generating
thousands of vectors costs a few numpy passes, not thousands of Python
loops over polynomials. Only the hashing (SHA-3 / SHAKE) is per item.

Only the ``*_internal`` functions of FIPS 203 (deterministic, explicit
randomness) are provided; callers pass the seeds and messages, which is
what reproducible test vectors need.
"""

from __future__ import annotations

import hashlib

import numpy as np

from mlkem_ref import (C1_LEN, CT_LEN, DK_LEN, DU, DV, EK_LEN, K, Q, basemul,
                       byte_decode, byte_encode, compress, decompress, intt, ntt)

ETA1 = ETA2 = 2                # ML-KEM-768
EK_PKE_LEN = 384 * K           # ByteEncode12(t_hat), without rho
SS_LEN = 32


# --- hash functions (FIPS 203 section 4.1) -------------------------------------
def H(s: bytes) -> bytes:
    return hashlib.sha3_256(s).digest()


def J(s: bytes) -> bytes:
    return hashlib.shake_256(s).digest(32)


def G(c: bytes) -> tuple[bytes, bytes]:
    d = hashlib.sha3_512(c).digest()
    return d[:32], d[32:]


def prf(eta: int, s: bytes, b: int) -> bytes:
    return hashlib.shake_256(s + bytes([b])).digest(64 * eta)


# --- sampling (Algorithms 7 and 8) ------------------------------------------------
def sample_ntt(seed34: bytes) -> np.ndarray:
    """SampleNTT: rejection-sample 256 coefficients < q from SHAKE128(rho||j||i)."""
    # 3 * 256 bytes give 512 candidates; ~81% survive, so 168*5 bytes (one
    # SHAKE128 rate block is 168 B) almost always suffice -- loop if not.
    out = np.empty(0, dtype=np.int64)
    nbytes = 168 * 5
    while True:
        buf = np.frombuffer(hashlib.shake_128(seed34).digest(nbytes), dtype=np.uint8).astype(np.int64)
        buf = buf[: len(buf) // 3 * 3].reshape(-1, 3)
        d1 = buf[:, 0] + 256 * (buf[:, 1] & 0x0F)
        d2 = (buf[:, 1] >> 4) + 16 * buf[:, 2]
        cand = np.stack([d1, d2], axis=1).reshape(-1)   # d1 before d2, block by block
        out = cand[cand < Q]
        if out.size >= 256:
            return out[:256]
        nbytes += 168


def sample_cbd(b: np.ndarray, eta: int) -> np.ndarray:
    """SamplePolyCBD_eta over the last axis: ``(..., 64*eta)`` bytes -> ``(..., 256)``."""
    bits = np.unpackbits(np.asarray(b, dtype=np.uint8), axis=-1, bitorder="little").astype(np.int64)
    bits = bits.reshape(b.shape[:-1] + (256, 2 * eta))
    x = bits[..., :eta].sum(axis=-1)
    y = bits[..., eta:].sum(axis=-1)
    return (x - y) % Q


def matrix_a(rho: bytes) -> np.ndarray:
    """A_hat[i][j] = SampleNTT(rho || j || i), shape ``(K, K, 256)``."""
    return np.array([[sample_ntt(rho + bytes([j, i])) for j in range(K)] for i in range(K)])


def _cbd_vectors(seeds: list[bytes], n0: int, count: int, eta: int) -> np.ndarray:
    """``count`` CBD polynomials per seed, PRF counters n0..n0+count-1: ``(N, count, 256)``."""
    raw = np.array([[np.frombuffer(prf(eta, s, n0 + t), dtype=np.uint8) for t in range(count)]
                    for s in seeds])
    return sample_cbd(raw, eta)


# --- K-PKE (Algorithms 13-15) -------------------------------------------------------
def kpke_keygen(d: bytes) -> tuple[bytes, bytes]:
    rho, sigma = G(d + bytes([K]))
    a_hat = matrix_a(rho)
    s = _cbd_vectors([sigma], 0, K, ETA1)[0]
    e = _cbd_vectors([sigma], K, K, ETA1)[0]
    s_hat, e_hat = ntt(s), ntt(e)
    t_hat = np.zeros((K, 256), dtype=np.int64)
    for i in range(K):
        for j in range(K):
            t_hat[i] = (t_hat[i] + basemul(a_hat[i, j], s_hat[j])) % Q
    t_hat = (t_hat + e_hat) % Q
    ek = byte_encode(t_hat, 12).tobytes() + rho
    dk = byte_encode(s_hat, 12).tobytes()
    return ek, dk


def kpke_encrypt_batch(ek: bytes, ms: list[bytes], rs: list[bytes],
                       a_hat: np.ndarray | None = None) -> np.ndarray:
    """K-PKE.Encrypt for N (message, randomness) pairs: ``(N, 1088)`` uint8."""
    n = len(ms)
    t_hat = byte_decode(np.frombuffer(ek[:EK_PKE_LEN], dtype=np.uint8).reshape(K, 384), 12)
    if a_hat is None:
        a_hat = matrix_a(ek[EK_PKE_LEN:])
    y = _cbd_vectors(rs, 0, K, ETA1)                   # (N, K, 256)
    e1 = _cbd_vectors(rs, K, K, ETA2)                  # (N, K, 256)
    e2 = _cbd_vectors(rs, 2 * K, 1, ETA2)[:, 0]        # (N, 256)
    y_hat = ntt(y)
    # u[i] = sum_j A_hat[j][i] * y_hat[j]  (A transposed)
    u_hat = np.zeros((n, K, 256), dtype=np.int64)
    for i in range(K):
        for j in range(K):
            u_hat[:, i] = (u_hat[:, i] + basemul(a_hat[j, i], y_hat[:, j])) % Q
    u = (intt(u_hat) + e1) % Q
    v_hat = np.zeros((n, 256), dtype=np.int64)
    for j in range(K):
        v_hat = (v_hat + basemul(t_hat[j], y_hat[:, j])) % Q
    m_bits = np.unpackbits(np.frombuffer(b"".join(ms), dtype=np.uint8).reshape(n, 32),
                           axis=-1, bitorder="little").astype(np.int64)
    mu = decompress(m_bits, 1)
    v = (intt(v_hat) + e2 + mu) % Q
    c1 = byte_encode(compress(u, DU), DU).reshape(n, C1_LEN)
    c2 = byte_encode(compress(v, DV), DV)
    return np.concatenate([c1, c2], axis=1).astype(np.uint8)


def kpke_decrypt_batch(dk_pke: bytes, cts: np.ndarray) -> np.ndarray:
    """K-PKE.Decrypt for ``(N, 1088)`` ciphertexts: ``(N, 32)`` uint8 messages."""
    cts = np.atleast_2d(np.asarray(cts, dtype=np.uint8))
    n = cts.shape[0]
    u = decompress(byte_decode(cts[:, :C1_LEN].reshape(n, K, 32 * DU), DU), DU)
    v = decompress(byte_decode(cts[:, C1_LEN:], DV), DV)
    s_hat = byte_decode(np.frombuffer(dk_pke, dtype=np.uint8).reshape(K, 384), 12)
    u_hat = ntt(u)
    acc = np.zeros((n, 256), dtype=np.int64)
    for i in range(K):
        acc = (acc + basemul(s_hat[i], u_hat[:, i])) % Q
    w = (v - intt(acc)) % Q
    return np.packbits(compress(w, 1).astype(np.uint8), axis=-1, bitorder="little")


# --- ML-KEM (Algorithms 16-18) --------------------------------------------------------
def keygen(d: bytes, z: bytes) -> tuple[bytes, bytes]:
    """ML-KEM.KeyGen_internal(d, z) -> (ek, dk)."""
    if len(d) != 32 or len(z) != 32:
        raise ValueError("d and z must be 32 bytes each")
    ek, dk_pke = kpke_keygen(d)
    return ek, dk_pke + ek + H(ek) + z


def encaps_batch(ek: bytes, ms: list[bytes], a_hat: np.ndarray | None = None
                 ) -> tuple[np.ndarray, np.ndarray]:
    """ML-KEM.Encaps_internal for each message: ``(K (N,32), c (N,1088))``."""
    if len(ek) != EK_LEN:
        raise ValueError("ek must be %d bytes" % EK_LEN)
    h_ek = H(ek)
    kr = [G(m + h_ek) for m in ms]
    cts = kpke_encrypt_batch(ek, ms, [r for _, r in kr], a_hat)
    keys = np.frombuffer(b"".join(k for k, _ in kr), dtype=np.uint8).reshape(len(ms), SS_LEN)
    return keys, cts


def encaps(ek: bytes, m: bytes) -> tuple[bytes, bytes]:
    keys, cts = encaps_batch(ek, [m])
    return keys[0].tobytes(), cts[0].tobytes()


def decaps_batch(dk: bytes, cts: np.ndarray, a_hat: np.ndarray | None = None
                 ) -> tuple[np.ndarray, np.ndarray]:
    """ML-KEM.Decaps_internal for ``(N, 1088)`` ciphertexts.

    Returns ``(K (N, 32), valid (N,) bool)``; ``valid`` is False where the
    re-encryption check failed and K is the implicit-rejection key J(z||c).
    """
    if len(dk) != DK_LEN:
        raise ValueError("dk must be %d bytes" % DK_LEN)
    cts = np.atleast_2d(np.asarray(cts, dtype=np.uint8))
    dk_pke, ek = dk[:EK_PKE_LEN], dk[EK_PKE_LEN:EK_PKE_LEN + EK_LEN]
    h, z = dk[EK_PKE_LEN + EK_LEN:EK_PKE_LEN + EK_LEN + 32], dk[EK_PKE_LEN + EK_LEN + 32:]
    ms = [m.tobytes() for m in kpke_decrypt_batch(dk_pke, cts)]
    kr = [G(m + h) for m in ms]
    re_cts = kpke_encrypt_batch(ek, ms, [r for _, r in kr], a_hat)
    valid = np.all(re_cts == cts, axis=1)
    keys = np.empty((cts.shape[0], SS_LEN), dtype=np.uint8)
    for i in range(cts.shape[0]):
        k = kr[i][0] if valid[i] else J(z + cts[i].tobytes())
        keys[i] = np.frombuffer(k, dtype=np.uint8)
    return keys, valid


def decaps(dk: bytes, ct: bytes) -> bytes:
    keys, _ = decaps_batch(dk, np.frombuffer(ct, dtype=np.uint8))
    return keys[0].tobytes()


def ek_from_dk(dk: bytes) -> bytes:
    return dk[EK_PKE_LEN:EK_PKE_LEN + EK_LEN]


assert CT_LEN == C1_LEN + 32 * DV
