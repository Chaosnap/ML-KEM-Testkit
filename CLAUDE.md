# pqc-testkit

## Project overview

Post-quantum cryptography test and benchmarking framework targeting FPGA hardware. Bridges the gap between NIST PQC standards (FIPS 203/204/205) and hardware validation - no existing open tool does this.

## Architecture

- **Go CLI** (`cmd/pqc-testkit/`) - cobra-based, commands: `bench`, `kat`, `fpga`
- **Algorithm wrappers** (`pkg/algorithms/`) - unified `Suite` interface over ML-KEM (stdlib `crypto/mlkem`), ML-DSA and SLH-DSA (cloudflare/circl)
- **KAT parser** (`pkg/kat/`) - NIST `.rsp` format parser
- **FPGA comms** (`pkg/fpga/`) - transport-agnostic `Device` interface with PCIe XDMA, AXI UIO, UART implementations
- **Benchmarking** (`pkg/bench/`) - statistical: median, p99, min/max, ops/sec
- **SCA** (`pkg/sca/`) - TVLA test vector generation + Welch's t-test
- **HDL** (`hdl/core/`) - vendor-agnostic SystemVerilog: Keccak-f1600, sponge, NTT butterfly, NTT engine

## Key design decisions

- `Device` interface is the contract between host and FPGA. Any board/transport implements it without changing test logic.
- CSR register map (offsets in `pkg/fpga/device.go`) is fixed across vendors. Vendor-specific code is only at the transport level.
- NTT butterfly is parameterized for both q=3329 (ML-KEM) and q=8380417 (ML-DSA).
- Papers bibliography is Go data (`docs/papers/index.go`), not markdown - queryable by category, algorithm, FPGA.

## Build and test

```bash
go build ./...
go test ./... -v -race
go run ./cmd/pqc-testkit bench -a ml-kem -n 100
```

## What's done

- Twiddle factor generation: `gen-twiddle` command produces hex files for NTT synthesis
- Barrett reduction: proper modular arithmetic in `mod_reduce.sv` (no `%` operator)
- KAT vector generation: `gen-vectors` produces .rsp files from software reference
- End-to-end validation: `gen-vectors` -> `kat` validates generated vectors
- FPGA simulation transport: `fpga --transport sim` demos full flow without hardware
- Complete Arty A7 board wrapper with UART bridge, constraints, status LEDs
- Keccak FSM race condition fixed (registered start pulse)
- Status signals wired from core to board-level indicators

## Known gaps (next work items)

1. No ACVP JSON format support (FIPS 140-3 labs need this)
2. HDL cores have no cocotb simulation testbenches
3. ML-KEM datapath FSM is structural (Keccak + NTT wired) but does not implement full algorithm math - integrate open-source cores (GMUCERG, KULeuven-COSIC) for algorithm-complete implementations
4. UART bridge byte ordering needs simulation verification

## Conventions

- MIT license
- Proper godoc on all exported symbols
- Tests in `_test.go` files alongside source
- HDL is vendor-agnostic in `hdl/core/`, vendor wrappers in `hdl/xilinx/` and `hdl/intel/`
- Board specs are data in `pkg/fpga/boards.go`, not code
