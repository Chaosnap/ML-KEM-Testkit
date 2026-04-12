.PHONY: build test bench lint vectors twiddle simulate clean

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
