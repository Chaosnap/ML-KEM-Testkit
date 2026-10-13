"""FIPS 203 helpers and the BaseMul leakage model for the CPA evaluation.

Only what the evaluation needs, ML-KEM-768 only: ByteDecode / Decompress,
the NTT / INTT, BaseCaseMultiply, parsing of ``dk`` and ``c``, and the
hypothesis model ``HD(R_prev, BaseMul(u_hat, s_hat_guess) mod q)``.

Everything here is vectorised over a leading "trace" axis so the same code
serves the synthetic scope, the acquisition script and the analysis.

``selftest()`` checks the whole chain against the real ML-KEM-768 reference
(the Go implementation behind ``pqc-testkit sca gen``): it decrypts a valid
ciphertext in software and must recover the message it was built from. If
that fails, û or ŝ are wrong and a CPA on the hardware would silently
"find nothing" -- so run it first.
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
    little-endian bit order both ways); only used to build synthetic/chosen
    ciphertexts (structured_ciphertext below), never on data from the board.
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
    """The message ``pkg/sca`` encrypts for the fixed class of a given seed."""
    return hashlib.sha3_256(b"pqc-testkit tvla fixed m" + seed).digest()


# --- leakage model -------------------------------------------------------------
_HW12 = np.array([bin(i).count("1") for i in range(4096)], dtype=np.int16)

#: What the guess is compared against (the register content being written).
TARGETS = {
    "p_self": 12,   # a_j * g mod q: the single Barrett product (registers p00 / p11)
    "c0": 12,       # even BaseMul output coefficient (+ accumulated C)
    "c1": 12,       # odd BaseMul output coefficient (+ accumulated C)
    "word": 24,     # {c1, c0}: the 24-bit RAM word / wb / rb_din register
}
#: What the register held before (R_prev).
RPREVS = {
    "zero": "R_prev = 0 (Hamming weight; precharged or cleared register)",
    "prev-word": "R_prev = the previous word of the same pass (register rewritten word after word)",
    "acc": "R_prev = C_old, the partial sum of the earlier polynomials (read-modify-write of the RAM word)",
}


def hamming_distance(x: np.ndarray, y: np.ndarray, bits: int) -> np.ndarray:
    z = np.bitwise_xor(x, y)
    if bits == 12:
        return _HW12[z & 0xFFF]
    return _HW12[z & 0xFFF] + _HW12[(z >> 12) & 0xFFF]


def accumulated_before(u_hat: np.ndarray, s_hat: np.ndarray, poly: int) -> np.ndarray | None:
    """C_old for BMUL ``poly``: sum_{i<poly} u_hat_i o s_hat_i, ``(N, 256)``."""
    if poly == 0:
        return None
    acc = np.zeros(u_hat.shape[:1] + (256,), dtype=np.int64)
    for i in range(poly):
        acc = (acc + basemul(u_hat[:, i, :], s_hat[i][None, :])) % Q
    return acc


class PairModel:
    """Hypotheses for one secret coefficient ``s_hat[poly][j]``.

    The guess ``g`` replaces coefficient ``j``; its partner in the pair and
    the accumulated value of earlier polynomials come from the true key.
    That is the usual known-key evaluation (and the "earlier coefficients
    already recovered" assumption of a sequential attack): it measures how
    far the *leakage* is from key recovery, not how good a search is.
    """

    def __init__(self, u_hat: np.ndarray, s_hat: np.ndarray, poly: int, j: int):
        self.u = u_hat[:, poly, :]
        self.s = s_hat[poly]
        self.c_old = accumulated_before(u_hat, s_hat, poly)
        self.poly, self.j, self.w, self.e = poly, j, j >> 1, j & 1
        self.true_value = int(self.s[j])

    # -- BaseMul pair w with coefficient j replaced by g ----------------------
    def _pair(self, g: np.ndarray, rows, w: int):
        e = self.e
        a0 = self.u[rows, 2 * w][:, None]
        a1 = self.u[rows, 2 * w + 1][:, None]
        b0 = int(self.s[2 * w])
        b1 = int(self.s[2 * w + 1])
        if g is not None:
            if e == 0:
                b0 = g[None, :]
            else:
                b1 = g[None, :]
        c0 = (a0 * b0 + (a1 * b1 % Q) * int(GAMMAS[w])) % Q
        c1 = (a0 * b1 + a1 * b0) % Q
        if self.c_old is not None:
            c0 = (c0 + self.c_old[rows, 2 * w][:, None]) % Q
            c1 = (c1 + self.c_old[rows, 2 * w + 1][:, None]) % Q
        return a0, a1, c0, c1

    def values(self, target: str, g: np.ndarray | None, rows=slice(None), w: int | None = None) -> np.ndarray:
        """Hypothetical register content, ``(N, G)`` (``G = 1`` if ``g`` is None)."""
        w = self.w if w is None else w
        a0, a1, c0, c1 = self._pair(g, rows, w)
        if target == "p_self":
            if g is None:
                gv = int(self.s[2 * w + self.e])
                return ((a0 if self.e == 0 else a1) * gv) % Q
            return ((a0 if self.e == 0 else a1) * g[None, :]) % Q
        if target == "c0":
            return c0
        if target == "c1":
            return c1
        if target == "word":
            return c0 | (c1 << 12)
        raise ValueError("unknown target %r" % target)

    def r_prev(self, target: str, mode: str, rows=slice(None)) -> np.ndarray:
        """R_prev for every trace, ``(N,)``, from the true key."""
        n = self.u[rows].shape[0]
        zero = np.zeros(n, dtype=np.int64)
        if mode == "zero":
            return zero
        if mode == "acc":
            if self.c_old is None or target == "p_self":
                return zero
            lo, hi = self.c_old[rows, 2 * self.w], self.c_old[rows, 2 * self.w + 1]
            return {"c0": lo, "c1": hi, "word": lo | (hi << 12)}[target]
        if mode == "prev-word":
            if self.w == 0:
                return zero
            return self.values(target, None, rows, self.w - 1)[:, 0]
        raise ValueError("unknown R_prev mode %r" % mode)

    def hypotheses(self, target: str, mode: str, g: np.ndarray, rows=slice(None)) -> np.ndarray:
        """HD(R_prev, X(g)) as float32, ``(N, G)``."""
        x = self.values(target, g, rows)
        r = self.r_prev(target, mode, rows)[:, None]
        return hamming_distance(r, x, TARGETS[target]).astype(np.float32)

    def true_hypothesis(self, target: str, mode: str, rows=slice(None)) -> np.ndarray:
        x = self.values(target, None, rows)
        r = self.r_prev(target, mode, rows)[:, None]
        return hamming_distance(r, x, TARGETS[target])[:, 0].astype(np.float32)


#: Named presets for the model families this toolkit targets. Each value is
#: a dict (not a tuple: this is the contract any third-party model module
#: should follow if it defines its own MODEL_PRESETS -- see
#: model_registry.py / ATTACK_MODEL.md section 5.2) with:
#:   target, rprev   -- keys into TARGETS / RPREVS above
#:   desc             -- one-line summary (dropdown label)
#:   equation          -- the hypothesis formula, for display
#:   register           -- RTL register name(s), mlkem_poly_alu.sv
#:   pipeline_stage      -- when in the BMUL pipeline it's valid (T+n)
#:   required_inputs      -- what the hypothesis needs besides the guess
#:   limitations            -- what this specific model does NOT capture
#:   valid_rprevs            -- which RPREVS keys are physically meaningful
#:                              for this target (a GUI should not offer the
#:                              others: e.g. R_prev="acc" is a no-op for
#:                              p_self -- PairModel.r_prev() returns 0 for
#:                              it regardless, see mlkem_ref.py above)
#: HW and HD both attack the single scalar Barrett product a_j * s_guess mod
#: q (registers p00/p11, cycle T+11 of the BMUL pipeline -- ATTACK_MODEL.md
#: section 5.2); the only difference is whether R_prev is modelled as 0
#: (Hamming WEIGHT, since HD(0, X) = HW(X) identically) or the previous
#: pair's product (Hamming DISTANCE). The "basemul-*" entries are the full
#: BaseCaseMultiply-output models (word/c0/c1), R_prev = prev-word being the
#: physically closest-to-real-hardware default (README.md section 5).
MODEL_PRESETS = {
    "HW": {
        "target": "p_self", "rprev": "zero", "valid_rprevs": ["zero"],
        "desc": "HW scalar multiplication -- Hamming weight, no R_prev needed",
        "equation": "HW( u_hat[j] * s_guess mod q )",
        "register": "p00 / p11 (one Barrett multiplier's output latch)",
        "pipeline_stage": "T+11 of the BMUL pipeline (mlkem_poly_alu.sv lines 269-270)",
        "required_inputs": "u_hat[j] (public, from the known ciphertext). No R_prev: "
                           "HD(0, X) = HW(X) identically, so the previous register "
                           "content is assumed to be 0.",
        "limitations": "The register only holds this value for 1 clock cycle -- needs "
                       "enough probe bandwidth/sample rate to resolve it. Assuming "
                       "R_prev=0 ignores whatever the register actually held the cycle "
                       "before, which real CMOS registers never are (see mlkem_ref.py "
                       "module docstring on why HD, not HW, is the physically motivated "
                       "default for RTL registers).",
    },
    "HD": {
        "target": "p_self", "rprev": "prev-word", "valid_rprevs": ["zero", "prev-word"],
        "desc": "HD scalar multiplication -- same register as HW, with R_prev modelled",
        "equation": "HD( R_prev, u_hat[j] * s_guess mod q )",
        "register": "p00 / p11 (one Barrett multiplier's output latch)",
        "pipeline_stage": "T+11 of the BMUL pipeline (mlkem_poly_alu.sv lines 269-270)",
        "required_inputs": "u_hat[j] (public) + R_prev = the SAME physical register's "
                           "content from the previous BMUL pair (one pair earlier, same "
                           "multiplier alternates between a0*b0/a1*b1 every other cycle).",
        "limitations": "Same short 1-cycle window as HW. R_prev assumes the register is "
                       "a simple pipeline latch continuously overwritten pair-to-pair, "
                       "which is true for p00/p11 specifically but not derived from a "
                       "full gate-level model.",
    },
    "basemul-c0": {
        "target": "c0", "rprev": "prev-word", "valid_rprevs": ["zero", "prev-word", "acc"],
        "desc": "HD BaseMul c0 -- even output coefficient of the pair, accumulated",
        "equation": "HD( R_prev, (a0*b0 + gamma*a1*b1 + C_old) mod q )",
        "register": "h0", "pipeline_stage": "T+17 (mlkem_poly_alu.sv line 286)",
        "required_inputs": "u_hat[2w], u_hat[2w+1] (public); s_hat[2w] or s_hat[2w+1] "
                           "(the guess, whichever is even); the OTHER half of the pair "
                           "and C_old (earlier polynomials' accumulated sum) come from "
                           "already-recovered guesses in unknown-key mode, or the real "
                           "key in oracle-assisted mode -- see PairModel docstring.",
        "limitations": "The Karatsuba cross term and C_old both fold in coefficients "
                       "other than the one guessed, adding noise/dependence beyond the "
                       "single target coefficient.",
    },
    "basemul-c1": {
        "target": "c1", "rprev": "prev-word", "valid_rprevs": ["zero", "prev-word", "acc"],
        "desc": "HD BaseMul c1 -- odd output coefficient of the pair, accumulated",
        "equation": "HD( R_prev, (a0*b1 + a1*b0 + C_old) mod q )",
        "register": "h1d[4]", "pipeline_stage": "T+18 (mlkem_poly_alu.sv lines 283-285)",
        "required_inputs": "Same as basemul-c0, for the odd half of the pair.",
        "limitations": "Same as basemul-c0.",
    },
    "basemul-word": {
        "target": "word", "rprev": "prev-word", "valid_rprevs": ["zero", "prev-word", "acc"],
        "desc": "HD BaseMul word -- full 24-bit RAM write-back (default, closest to a real probe)",
        "equation": "HD( R_prev, {c1, c0} )  (c1 in bits 23:12, c0 in bits 11:0)",
        "register": "wb / wbd / rb_din",
        "pipeline_stage": "T+18-20 internally, written to the polynomial RAM at T+21 "
                          "(mlkem_poly_alu.sv lines 290-291, 301)",
        "required_inputs": "Everything basemul-c0 and basemul-c1 each need, combined.",
        "limitations": "Combines both pair halves into one 24-bit hypothesis, so a guess "
                       "for a single coefficient only explains half the register's bits "
                       "-- the strongest real-probe match of the five, per the validated "
                       "positive control (README.md section 3.3), but not a 'pure' "
                       "single-coefficient model.",
    },
}


def window_words(u_hat: np.ndarray, s_hat: np.ndarray, poly: int) -> tuple:
    """The 128 words written by BMUL ``poly`` for one trace, and their predecessors.

    ``u_hat`` / ``s_hat`` are ``(3, 256)``. Returns ``(words, prev_words)`` of
    shape ``(128,)``: word w is {c1, c0} of pair w *including* the partial sum
    of the earlier polynomials, as written back by the ALU. The synthetic
    scope leaks HD(prev, word) with it, i.e. the model the analysis tests with
    ``--target word --rprev prev-word``.
    """
    acc = np.zeros(256, dtype=np.int64)
    for i in range(poly + 1):
        acc = (acc + basemul(u_hat[i], s_hat[i])) % Q
    words = acc[0::2] | (acc[1::2] << 12)
    prev = np.concatenate([[0], words[:-1]])
    return words, prev


# --- structured (chosen-ciphertext) CPA vectors ------------------------------
def structured_ciphertext(poly: int, pair: int, rng: np.random.Generator) -> bytes:
    """A ciphertext whose u_hat is zero everywhere except one BaseMul pair.

    Sets u_hat[poly][2*pair] and u_hat[poly][2*pair+1] to fresh random values
    and every other coefficient (in all 3 polynomials) to zero, then encodes
    that back into valid ciphertext bytes. This isolates the one targeted
    BMUL pair's contribution: every *other* BMUL pair, in every polynomial,
    multiplies by zero and so cannot add noise to the accumulator (c_old
    stays exactly 0 up to the targeted pair) -- a template-attack-style
    technique to raise SNR at a chosen pair, the "structured chosen
    ciphertext" strategy in acquire_cpa.py --strategy structured.

    Only c1 (the three compressed-u chunks) needs constructing: Decaps reads
    c2 (v) only *after* all three BMULs finish (microcode ADDR 248, well
    after ADDR 238-246 -- see ATTACK_MODEL.md section 4.1), so c2's content
    cannot affect the attacked stage and is zero-filled here.

    The actual u_hat the hardware computes is whatever
    u_hat_from_ct() returns for the bytes produced here (compression at
    DU=10 bits is lossy, so it will be close to, not bit-exact with, the
    chosen values) -- callers must always recompute u_hat from the returned
    ciphertext bytes for the hypothesis, never assume the chosen values
    verbatim. This is not a valid encapsulation (no m, no ek involved): it
    is for isolating one BMUL pair's power contribution, not for producing a
    ciphertext anyone would accept as genuine -- the core's own implicit-
    rejection path still runs to completion and returns *something*, it is
    simply not a real encapsulated key.
    """
    u_hat = np.zeros((K, 256), dtype=np.int64)
    u_hat[poly, 2 * pair] = int(rng.integers(0, Q))
    u_hat[poly, 2 * pair + 1] = int(rng.integers(0, Q))
    u = intt(u_hat)
    c1 = byte_encode(compress(u, DU), DU).tobytes()
    c2 = bytes(CT_LEN - C1_LEN)
    return c1 + c2
