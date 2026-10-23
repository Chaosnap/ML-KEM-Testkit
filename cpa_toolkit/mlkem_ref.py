"""FIPS 203 helpers for the CPA evaluation (ML-KEM-768 only).

ByteDecode / Decompress, the NTT / INTT, BaseCaseMultiply, parsing of ``dk``
and ``c`` and K-PKE.Decrypt. The CPA hypotheses built on these are in
``cpa.py``.

Everything here is vectorised over a leading "trace" axis so the same code
serves the test-vector generator (``mlkem768.py`` builds on these
transforms), the acquisition and the analysis.

``selftest()`` checks the whole chain against the FIPS 203 reference
(``mlkem768.py``, itself checked against the NIST ACVP vectors): it decrypts
a valid ciphertext in software and must recover the message it was built
from. If that fails, û or ŝ are wrong and a CPA on the hardware would
silently "find nothing" -- so run it first.
"""

from __future__ import annotations

import hashlib

import numpy as np

Q = 3329
K = 3          # ML-KEM-768
DU, DV = 10, 4
DK_LEN, EK_LEN, CT_LEN = 2400, 1184, 1088
C1_LEN = 32 * DU * K          # 960 bytes of compressed u
INV128 = 3303                 # 128^-1 mod q


def _bitrev7(x: int) -> int:
    return int(format(x, "07b")[::-1], 2)


#: zeta^BitRev7(i), i = 0..127 (FIPS 203 Appendix A).
ZETAS = np.array([pow(17, _bitrev7(i), Q) for i in range(128)], dtype=np.int64)
#: gamma_i = zeta^(2 BitRev7(i) + 1), the BaseCaseMultiply constant of pair i.
GAMMAS = np.array([pow(17, 2 * _bitrev7(i) + 1, Q) for i in range(128)], dtype=np.int64)

assert ZETAS[1] == 1729 and GAMMAS[0] == 17


# --- encodings ---------------------------------------------------------------
def byte_decode(data, d: int) -> np.ndarray:
    """ByteDecode_d over the last axis: ``(..., 32*d)`` bytes -> ``(..., 256)``."""
    a = np.asarray(bytearray(data) if isinstance(data, (bytes, bytearray)) else data, dtype=np.uint8)
    bits = np.unpackbits(a, axis=-1, bitorder="little")
    bits = bits[..., : 256 * d].reshape(a.shape[:-1] + (256, d)).astype(np.int64)
    vals = (bits << np.arange(d, dtype=np.int64)).sum(axis=-1)
    return vals % Q if d == 12 else vals


def byte_encode(vals, d: int) -> np.ndarray:
    """ByteEncode_d (FIPS 203 Algorithm 4), inverse of byte_decode: last-axis
    ``(..., 256)`` integers in ``[0, 2**d)`` -> ``(..., 32*d)`` uint8 bytes.

    Exact mathematical inverse of byte_decode's bit unpacking (same
    little-endian bit order both ways); used to build ciphertexts (mlkem768.py), never on data from the board.
    """
    vals = np.asarray(vals, dtype=np.int64) & ((1 << d) - 1)
    bits = ((vals[..., None] >> np.arange(d, dtype=np.int64)) & 1).astype(np.uint8)
    bits = bits.reshape(vals.shape[:-1] + (256 * d,))
    return np.packbits(bits, axis=-1, bitorder="little")


def decompress(y: np.ndarray, d: int) -> np.ndarray:
    return (y * Q + (1 << (d - 1))) >> d


def compress(x: np.ndarray, d: int) -> np.ndarray:
    return (((x << (d + 1)) + Q) // (2 * Q)) % (1 << d)


# --- transforms --------------------------------------------------------------
def ntt(f: np.ndarray) -> np.ndarray:
    """FIPS 203 Algorithm 9 over the last axis (any leading shape)."""
    f = np.array(f, dtype=np.int64) % Q
    k, ln = 1, 128
    while ln >= 2:
        for start in range(0, 256, 2 * ln):
            z = int(ZETAS[k])
            k += 1
            t = (z * f[..., start + ln : start + 2 * ln]) % Q
            f[..., start + ln : start + 2 * ln] = (f[..., start : start + ln] - t) % Q
            f[..., start : start + ln] = (f[..., start : start + ln] + t) % Q
        ln >>= 1
    return f


def intt(f: np.ndarray) -> np.ndarray:
    """FIPS 203 Algorithm 10 over the last axis."""
    f = np.array(f, dtype=np.int64) % Q
    k, ln = 127, 2
    while ln <= 128:
        for start in range(0, 256, 2 * ln):
            z = int(ZETAS[k])
            k -= 1
            a = f[..., start : start + ln].copy()
            b = f[..., start + ln : start + 2 * ln].copy()
            f[..., start : start + ln] = (a + b) % Q
            f[..., start + ln : start + 2 * ln] = (z * (b - a)) % Q
        ln <<= 1
    return (f * INV128) % Q


def basemul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """MultiplyNTTs (Algorithms 11/12): 128 degree-1 products mod X^2 - gamma."""
    a0, a1 = a[..., 0::2], a[..., 1::2]
    b0, b1 = b[..., 0::2], b[..., 1::2]
    out = np.empty(np.broadcast_shapes(a.shape, b.shape), dtype=np.int64)
    out[..., 0::2] = (a0 * b0 + (a1 * b1 % Q) * GAMMAS) % Q
    out[..., 1::2] = (a0 * b1 + a1 * b0) % Q
    return out


# --- ML-KEM-768 objects --------------------------------------------------------
def s_hat_from_dk(dk: bytes) -> np.ndarray:
    """The secret vector in the NTT domain, ``(3, 256)``: dk[0:1152] decoded."""
    if len(dk) != DK_LEN:
        raise ValueError("dk must be %d bytes, got %d" % (DK_LEN, len(dk)))
    return byte_decode(np.frombuffer(dk[: 384 * K], dtype=np.uint8).reshape(K, 384), 12)


def u_hat_from_ct(ct: np.ndarray) -> np.ndarray:
    """û = NTT(Decompress_10(c1)) for a batch of ciphertexts.

    ``ct`` is ``(N, 1088)`` uint8; the result is ``(N, 3, 256)`` int64.
    """
    ct = np.atleast_2d(np.asarray(ct, dtype=np.uint8))
    c1 = ct[:, :C1_LEN].reshape(ct.shape[0], K, 32 * DU)
    return ntt(decompress(byte_decode(c1, DU), DU))


def decrypt(dk: bytes, ct: bytes) -> bytes:
    """K-PKE.Decrypt: returns the 32-byte message m'."""
    c = np.frombuffer(ct, dtype=np.uint8)
    s = s_hat_from_dk(dk)
    u = u_hat_from_ct(c[None, :])[0]
    v = decompress(byte_decode(c[C1_LEN:], DV), DV)
    acc = np.zeros(256, dtype=np.int64)
    for i in range(K):
        acc = (acc + basemul(s[i], u[i])) % Q
    w = (v - intt(acc)) % Q
    return np.packbits(compress(w, 1).astype(np.uint8), bitorder="little").tobytes()


def selftest(dk: bytes, ct: bytes, m_expected: bytes) -> bool:
    """True if software decryption of (dk, ct) gives ``m_expected``."""
    return decrypt(dk, ct) == m_expected


def fixed_message(seed: bytes) -> bytes:
    """The message the TVLA fixed class encapsulates for a given seed.

    The label is kept from the original Go generator so a seed gives the same
    fixed ciphertext as it always did."""
    return hashlib.sha3_256(b"pqc-testkit tvla fixed m" + seed).digest()
