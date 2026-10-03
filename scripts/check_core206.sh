#!/usr/bin/env bash
# Offline RTL-only checks. Uses existing Python, Icarus and Verilator;
# does not invoke synthesis/implementation tools or install dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in python3 iverilog vvp verilator make; do command -v "$tool" >/dev/null; done
out=build/core206_checks
mkdir -p "$out"
verilator --lint-only --timing -Wall -Wno-fatal --top-module pqc_mlkem_top hdl/core/*.sv > "$out/lint.log" 2>&1
if grep -E '%Error|%Warning-(WIDTH|LATCH|MULTIDRIVEN|UNOPTFLAT|CASEINCOMPLETE)' "$out/lint.log"; then
    exit 1
fi
make -C testbench/sponge > "$out/sponge.log" 2>&1
grep -E '^SHAKE128 block:|^tb_sponge:|^TB_SPONGE' "$out/sponge.log"
make -C testbench/units pack unpack fifo keccak > "$out/units.log" 2>&1
grep -E '^tb_pack:|^TB_' "$out/units.log"
python3 scripts/sim_acvp.py > "$out/acvp.log" 2>&1
cat "$out/acvp.log"
python3 scripts/sim_rejection_boundary.py > "$out/rejection_boundary.log" 2>&1
cat "$out/rejection_boundary.log"
make -C testbench/cdc > "$out/cdc.log" 2>&1
grep -E '^KeyGen:|^Decaps:|^TB_CDC' "$out/cdc.log"
make -C testbench/tvla_sim > "$out/tvla_build.log" 2>&1
echo 'TVLA model build PASS'
python3 scripts/profile_ucode.py --op all --md "$out/profile.md" --json "$out/profile.json" > "$out/profile.log" 2>&1
echo 'CORE206 RTL CHECKS PASS (physical timing/resource use not measured)'
