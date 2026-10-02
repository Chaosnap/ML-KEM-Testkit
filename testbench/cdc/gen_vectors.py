#!/usr/bin/env python3
"""First ML-KEM-768 ACVP KeyGen and Decaps vectors for tb_cdc.sv.

Usage: gen_vectors.py <out_dir>
Writes kg_in.hex (d||z), kg_out.hex (ek||dk), dc_in.hex (dk||c), dc_out.hex (K),
one byte per line.
"""

import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def write(path, data):
    with open(path, "w") as f:
        f.write("\n".join("%02x" % b for b in data) + "\n")


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(ROOT, "acvp", "ML-KEM-keyGen-FIPS203", "internalProjection.json")) as f:
        kg = next(g for g in json.load(f)["testGroups"] if g["parameterSet"] == "ML-KEM-768")["tests"][0]
    with open(os.path.join(ROOT, "acvp", "ML-KEM-encapDecap-FIPS203", "internalProjection.json")) as f:
        dc = next(g for g in json.load(f)["testGroups"]
                  if g["parameterSet"] == "ML-KEM-768" and g.get("function") == "decapsulation")["tests"][0]
    h = bytes.fromhex
    write(os.path.join(out, "kg_in.hex"), h(kg["d"] + kg["z"]))
    write(os.path.join(out, "kg_out.hex"), h(kg["ek"] + kg["dk"]))
    write(os.path.join(out, "dc_in.hex"), h(dc["dk"] + dc["c"]))
    write(os.path.join(out, "dc_out.hex"), h(dc["k"]))


if __name__ == "__main__":
    main()
