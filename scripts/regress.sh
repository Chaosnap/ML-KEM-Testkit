#!/usr/bin/env bash
# regress.sh - every simulation test of the repository, in one go.
#
#   scripts/regress.sh            (from the repository root)
#
# Steps: Go unit tests, Icarus UART/NTT/UART-link benches, cocotb Barrett
# (Icarus) and Keccak-f1600 (Verilator) tests if cocotb-config is on PATH,
# and the ML-KEM-768 ACVP vectors on the full core (scripts/sim_acvp.py).
# Exits non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n=== %s\n' "$*"; }

step "go test"
go test ./... -race -count=1 | grep -v "no test files"

step "iverilog: uart, ntt, uart-link"
make -s -C testbench/iverilog all | grep -E "PASS|FAIL"
make -s -C testbench/iverilog uart-link BAUD=1000000 | grep -E "PASS|FAIL"

if command -v cocotb-config >/dev/null; then
    step "cocotb: Barrett (icarus), Keccak-f1600 (verilator)"
    make -s -C testbench/ntt SIM=icarus 2>&1 | grep -E "TESTS="
    make -s -C testbench/keccak SIM=verilator 2>&1 | grep -E "TESTS="
    if grep -q '<failure' testbench/ntt/results.xml testbench/keccak/results.xml; then exit 1; fi
else
    step "cocotb: SKIPPED (cocotb-config not on PATH)"
fi

step "ACVP ML-KEM-768 on the core (Verilator)"
python3 scripts/sim_acvp.py

step "REGRESS PASS"
