.PHONY: build test bench lint vectors twiddle ucode tvla-sim sim-iverilog simulate clean

# ML-KEM levels in the microcode ROM; must include FIXED_LEVEL of pqc_mlkem_top.
MLKEM_LEVELS ?= 768

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

# Simulation-based TVLA on the RTL (docs/SIM_TVLA_GUIDE.md).
tvla-sim: build
	$(MAKE) -C testbench/tvla_sim
	./pqc-testkit sca sim --op decaps -n 200 -o build/tvla_sim

# Icarus Verilog testbenches for UART + NTT, VCDs in build/sim/
# (docs/IVERILOG_SIM_GUIDE.md).
sim-iverilog:
	$(MAKE) -C testbench/iverilog

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
