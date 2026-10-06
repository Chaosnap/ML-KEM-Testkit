.PHONY: build test bench lint vectors twiddle ucode tvla-sim sim-iverilog acvp-sim acvp-uart regress simulate clean

# ML-KEM levels in the microcode ROM (the core supports 768 only).
MLKEM_LEVELS ?= 768

# Python of the project environment (pyproject.toml: pyserial, matplotlib, numpy).
PY ?= uv run python

# Build the CLI binary.
build:
	go build -o pqc-testkit ./cmd/pqc-testkit

# Run all Go tests.
test:
	go test ./... -v -race -count=1

# Run PQC benchmarks (quick smoke test).
bench:
	go run ./cmd/pqc-testkit bench -a ml-kem -n 100
	go run ./cmd/pqc-testkit bench -a ml-dsa -n 20
	go run ./cmd/pqc-testkit bench -a slh-dsa -n 2

# Generate KAT test vectors from software reference.
vectors:
	go run ./cmd/pqc-testkit gen-vectors -a ml-kem -o vectors/ -n 10
	go run ./cmd/pqc-testkit gen-vectors -a ml-dsa -o vectors/ -n 5

# Generate NTT twiddle factor hex files for HDL synthesis.
twiddle:
	go run ./cmd/pqc-testkit gen-twiddle -o hdl/core

# Regenerate the ML-KEM microcode and zeta ROMs.
ucode:
	python3 scripts/gen_mlkem_ucode.py --levels $(MLKEM_LEVELS)

# Simulated TVLA of the RTL, plots and report in sim_result/tvla/ (docs/TVLA_SIM.md).
tvla-sim:
	$(PY) scripts/tvla_sim.py

# Icarus Verilog testbenches for UART + NTT, VCDs in build/sim/.
sim-iverilog:
	$(MAKE) -C testbench/iverilog

# NIST ACVP ML-KEM-768 vectors on the RTL in Verilator, report in
# sim_result/acvp/ (docs/ACVP_TEST.md).
acvp-sim:
	$(PY) scripts/acvp_test.py sim

# The same vectors on the FPGA board over UART:  make acvp-uart PORT=/dev/cu.usbserial-XXXX1
acvp-uart:
	@test -n "$(PORT)" || (echo "usage: make acvp-uart PORT=<serial port>"; exit 2)
	$(PY) scripts/acvp_test.py uart -p $(PORT)

# All simulation tests: Go, Icarus benches, unit tests, sponge, CDC system
# test, TVLA model build, ACVP on the core.
regress:
	go test ./... -count=1
	$(MAKE) -C testbench/iverilog all
	$(MAKE) -C testbench/iverilog uart-link BAUD=1000000
	$(MAKE) -C testbench/sponge
	$(MAKE) -C testbench/units all
	$(MAKE) -C testbench/cdc
	$(MAKE) -C testbench/tvla_sim
	$(PY) scripts/acvp_test.py sim

# Run FPGA validation in simulation mode (no hardware needed).
simulate:
	go run ./cmd/pqc-testkit fpga --transport sim

# Lint HDL cores with Verilator.
lint:
	verilator --lint-only -Wall hdl/core/keccak_f1600.sv
	verilator --lint-only -Wall hdl/core/uart_rx.sv
	verilator --lint-only -Wall hdl/core/uart_tx.sv
	verilator --lint-only -Wall hdl/core/mod_reduce.sv

# Vet Go code.
vet:
	go vet ./...

# Clean build artifacts.
clean:
	rm -f pqc-testkit
	rm -rf build/
	rm -f hdl/core/twiddle_*.hex
	rm -f vectors/**/*.rsp
