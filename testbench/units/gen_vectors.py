#!/usr/bin/env python3
"""Reference vectors for tb_pack.sv / tb_unpack.sv (FIPS 203 section 4.2.1).

Written straight from the FIPS 203 definitions with exact integer rounding,
independent of the RTL's multiply-shift tricks:
  Compress_d(x)   = round(2^d / q * x) mod 2^d     (round half up)
  Decompress_d(y) = round(q / 2^d * y)
  ByteEncode_d / ByteDecode_d: little-endian d-bit fields.

Usage: gen_vectors.py <out_dir>
Writes (one hex value per line, $readmemh format):
  pack_coefs.hex          NP_PACK x 256 coefficients: every x in [0, q), then random
  pack_exp_d<d>.hex       ByteEncode_d(Compress_d(poly)) for each poly (d = 12: no compress)
  unpack_bytes_d<d>.hex   ByteEncode_d(fields) for each poly; fields = every value in
                          [0, 2^d), then random; max(2, 2^d / 256) polys
  unpack_exp_d<d>.hex     expected coefficients (Decompress_d, or field mod q for d = 12)
  unpack_err_d<d>.hex     per poly: number of fields >= q (d = 12 modulus check)
  sample_bytes.hex        NP_SAMPLE x 672 random bytes (4 SHAKE128 blocks)
  sample_exp.hex          SampleNTT (FIPS 203 Algorithm 7) of each 672-byte string
  cbd<eta>_bytes.hex      NP_CBD x 64*eta random bytes
  cbd<eta>_exp.hex        SamplePolyCBD_eta (FIPS 203 Algorithm 8), eta = 2, 3
"""

import os
import random
import sys

Q = 3329
PACK_DS = list(range(1, 13))
UNPACK_DS = list(range(1, 13))
NP_SAMPLE, SAMPLE_BYTES = 8, 672
NP_CBD = 4


def sample_ntt(b):
    """FIPS 203 Algorithm 7 on a fixed byte string (must yield 256)."""
    a, i = [], 0
    while len(a) < 256:
        d1 = b[i] + 256 * (b[i + 1] % 16)
        d2 = b[i + 1] // 16 + 16 * b[i + 2]
        if d1 < Q:
            a.append(d1)
        if d2 < Q and len(a) < 256:
            a.append(d2)
        i += 3
    return a


def cbd(b, eta):
    """FIPS 203 Algorithm 8."""
    bits = [(b[i // 8] >> (i % 8)) & 1 for i in range(8 * len(b))]
    out = []
    for i in range(256):
        x = sum(bits[2 * i * eta + j] for j in range(eta))
        y = sum(bits[2 * i * eta + eta + j] for j in range(eta))
        out.append((x - y) % Q)
    return out


def compress(x, d):
    return ((x << (d + 1)) + Q) // (2 * Q) % (1 << d)


def decompress(y, d):
    return (2 * Q * y + (1 << d)) >> (d + 1)


def byte_encode(vals, d):
    acc = 0
    for i, v in enumerate(vals):
        acc |= v << (d * i)
    return list(acc.to_bytes(32 * d, "little"))


def polys_covering(values, rng, limit):
    """Split values into 256-coefficient polys, padding the last with random ones."""
    vals = list(values)
    while len(vals) % 256:
        vals.append(rng.randrange(limit))
    return [vals[i:i + 256] for i in range(0, len(vals), 256)]


def write(path, vals, width):
    with open(path, "w") as f:
        f.write("\n".join("%0*x" % (width, v) for v in vals) + "\n")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    rng = random.Random(203)

    polys = polys_covering(range(Q), rng, Q)
    write(os.path.join(out, "pack_coefs.hex"), [c for p in polys for c in p], 3)
    for d in PACK_DS:
        exp = []
        for p in polys:
            exp += byte_encode(p if d == 12 else [compress(x, d) for x in p], d)
        write(os.path.join(out, "pack_exp_d%d.hex" % d), exp, 2)

    for d in UNPACK_DS:
        fp = polys_covering(range(1 << d), rng, 1 << d)
        if len(fp) < 2:                           # At least two polys per d.
            fp.append([rng.randrange(1 << d) for _ in range(256)])
        data, exp, err = [], [], []
        for p in fp:
            data += byte_encode(p, d)
            exp += [(f - Q if f >= Q else f) if d == 12 else decompress(f, d) for f in p]
            err.append(sum(f >= Q for f in p) if d == 12 else 0)
        write(os.path.join(out, "unpack_bytes_d%d.hex" % d), data, 2)
        write(os.path.join(out, "unpack_exp_d%d.hex" % d), exp, 3)
        write(os.path.join(out, "unpack_err_d%d.hex" % d), err, 3)
    data, exp = [], []
    for _ in range(NP_SAMPLE):
        b = [rng.randrange(256) for _ in range(SAMPLE_BYTES)]
        data += b
        exp += sample_ntt(b)
    write(os.path.join(out, "sample_bytes.hex"), data, 2)
    write(os.path.join(out, "sample_exp.hex"), exp, 3)
    for eta in (2, 3):
        data, exp = [], []
        for _ in range(NP_CBD):
            b = [rng.randrange(256) for _ in range(64 * eta)]
            data += b
            exp += cbd(b, eta)
        write(os.path.join(out, "cbd%d_bytes.hex" % eta), data, 2)
        write(os.path.join(out, "cbd%d_exp.hex" % eta), exp, 3)
    print("vectors: pack %d polys, unpack d=%s, sample %d, cbd 2x%d"
          % (len(polys), UNPACK_DS, NP_SAMPLE, NP_CBD))


if __name__ == "__main__":
    main()
