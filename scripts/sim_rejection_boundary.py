#!/usr/bin/env python3
"""Exercise first/last ciphertext comparisons and sticky-flag clearing in RTL.

Uses local ACVP key material and hashlib SHAKE256 as the independent oracle
for ML-KEM implicit rejection. No downloads or external Python packages.
"""
import hashlib
import json
from pathlib import Path

import coresim


def main():
    path = Path(coresim.ROOT) / 'acvp/ML-KEM-keyGen-FIPS203/internalProjection.json'
    groups = json.loads(path.read_text())['testGroups']
    case = next(g for g in groups if g['parameterSet'] == 'ML-KEM-768')['tests'][0]
    ek, dk = (bytes.fromhex(case[k]) for k in ('ek', 'dk'))
    enc = coresim.run([coresim.Job(1, 768, ek + bytes(range(32)))])[0]
    assert enc.status == 2 and enc.err == 0
    ct, key = enc.out[:1088], enc.out[1088:]
    jobs, expected, labels = [], [], []
    # Include each byte of the first and last comparison words. Valid inputs
    # between mutations check that a previous rejection cannot poison a run.
    for index in (0, 1, 2, 3, 1084, 1085, 1086, 1087):
        modified = bytearray(ct)
        modified[index] ^= 1
        modified = bytes(modified)
        jobs.extend([coresim.Job(2, 768, dk + modified), coresim.Job(2, 768, dk + ct)])
        expected.extend([hashlib.shake_256(dk[-32:] + modified).digest(32), key])
        labels.extend([f'mutation at ciphertext byte {index}', 'valid after rejection'])
    for result, want, label in zip(coresim.run(jobs), expected, labels):
        assert result.status == 2 and result.err == 0 and result.out == want, label
    print(f'IMPLICIT_REJECTION_BOUNDARY PASS {len(jobs)}/{len(jobs)}')


if __name__ == '__main__':
    main()
