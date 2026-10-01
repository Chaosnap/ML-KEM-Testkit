#!/usr/bin/env python3
"""SHA-3 / SHAKE vectors for tb_sponge.sv, from Python hashlib.

Usage: gen_vectors.py <out_file>
Format (whitespace separated, hex bytes without 0x):
  <ntests>
  <mode> <msglen> <outlen> <msg bytes...> <expected output bytes...>
mode: 0 SHA3-256, 1 SHA3-512, 2 SHAKE128, 3 SHAKE256 (keccak_sponge.sv).
"""

import hashlib
import random
import sys

RATE = {0: 136, 1: 72, 2: 168, 3: 136}


def digest(mode, msg, outlen):
    if mode == 0:
        return hashlib.sha3_256(msg).digest()
    if mode == 1:
        return hashlib.sha3_512(msg).digest()
    if mode == 2:
        return hashlib.shake_128(msg).digest(outlen)
    return hashlib.shake_256(msg).digest(outlen)


def main():
    rng = random.Random(202)
    tests = []
    for mode in range(4):
        r = RATE[mode]
        lens = sorted({0, 1, 3, 7, 8, 9, 31, 32, 33, 34, r - 1, r, r + 1, 2 * r - 3, 2 * r, 3 * r + 5,
                       rng.randrange(1, 4 * r)})
        for ml in lens:
            if mode < 2:
                outs = [32 if mode == 0 else 64]
            else:
                outs = [1, 7, 32, r - 1, r, r + 1, 3 * r + 13] if ml in (0, 33, r) else [rng.choice([5, 64, 2 * r + 7])]
            for ol in outs:
                msg = bytes(rng.randrange(256) for _ in range(ml))
                tests.append((mode, msg, digest(mode, msg, ol)))
    with open(sys.argv[1], "w") as f:
        f.write("%d\n" % len(tests))
        for mode, msg, out in tests:
            f.write("%d %d %d\n" % (mode, len(msg), len(out)))
            f.write(" ".join("%02x" % b for b in msg) + "\n")
            f.write(" ".join("%02x" % b for b in out) + "\n")
    print("%d sponge vectors" % len(tests))


if __name__ == "__main__":
    main()
