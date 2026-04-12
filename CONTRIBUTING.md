# Contributing to pqc-testkit

There are three ways to contribute depending on what you have access to.

## Path 1: I have an FPGA board

This is the most valuable contribution. We need hardware validation data.

### What to do

1. Clone the repo and verify software works:
```bash
git clone https://github.com/akhilesharora/pqc-testkit.git
cd pqc-testkit
make test
make simulate
```

2. Synthesize for your board:
```bash
# Arty A7 (UART)
vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem

# Or add support for your own board (see "Adding board support" below)
```

3. Program and run validation:
```bash
./pqc-testkit fpga -T uart -d /dev/ttyUSB1
```

4. Report results: open an issue with your board name, synthesis results (LUTs/FFs/BRAMs/MHz from the utilization report), and test output.

### Adding board support

If your board isn't listed, add it:

1. Add board specs to `pkg/fpga/boards.go`:
```go
"your-board": {
    Name:   "Your Board",
    Vendor: "xilinx",  // or "intel"
    Family: "Artix-7",
    Part:   "xc7a35ticsg324-1L",
    LUTs:   20800,
    // ... etc
},
```

2. Create a board directory: `hdl/xilinx/your_board/` or `hdl/intel/your_board/`

3. Add a top-level wrapper (see `hdl/xilinx/arty_a7/arty_a7_top.sv` as reference) and constraints file.

4. Submit a PR with the board support.

## Path 2: I have a PQC hardware core

If you've implemented ML-KEM, ML-DSA, or SLH-DSA in RTL (or have a working core from a research project), you can integrate it with the test kit.

### What to do

1. Your core needs to implement the CSR register map defined in `pkg/fpga/doc.go`:
```
0x00 CTRL        RW  bit 0=start, bit 1=reset
0x04 STATUS      RO  bit 0=busy, bit 1=done, bit 2=error
0x08 ALG_ID      RO  1=ML-KEM, 2=ML-DSA, 3=SLH-DSA
0x0C SEC_LEVEL   RW  security level parameter
0x10 OP_MODE     RW  0=keygen, 1=encaps/sign, 2=decaps/verify
0x14 CYCLE_COUNT RO  clock cycles for last operation
0x18 VERSION     RO  packed version number
```

2. Use `pqc_axi_csr.sv` as the CSR register file - it handles AXI-Lite protocol and gives you clean control signals to wire into your datapath.

3. Use the reference building blocks in `hdl/core/` if they help:
   - `keccak_f1600.sv` - Keccak permutation
   - `ntt_engine.sv` - NTT with Barrett reduction
   - `mod_reduce.sv` - Modular arithmetic for q=3329 and q=8380417

4. Validate your integration:
```bash
# Generate reference vectors
make vectors

# Test with sim first to verify host-side
make simulate

# Then test on hardware
./pqc-testkit fpga -T uart -d /dev/ttyUSB1 -v vectors/
```

5. Submit a PR with your core and validation results.

### Open-source cores to start from

These are the ones we've indexed in `docs/papers/index.go` that have open-source HDL:

| Repo | Algorithm | Link |
|------|-----------|------|
| GMUCERG/Dilithium | ML-DSA (Verilog) | github.com/GMUCERG/Dilithium |
| KULeuven-COSIC/ML-DSA-OSH | ML-DSA FIPS 204 | github.com/KULeuven-COSIC/ML-DSA-OSH |
| slh-dsa/sloth | SLH-DSA + RISC-V | github.com/slh-dsa/sloth |
| caslab-code/pqc-hw-sphincslet | SLH-DSA | github.com/caslab-code/pqc-hw-sphincslet |

The integration work is: wrap their datapath behind `pqc_axi_csr.sv` so the test kit can talk to it.

## Path 3: I don't have hardware

Plenty to do on the software side.

### What to do

- **ACVP JSON support**: Parse NIST ACVP JSON test vectors (the format FIPS 140-3 labs use). See `pkg/kat/` for the existing .rsp parser.
- **New algorithm support**: Add ML-KEM-512 via circl, or add FALCON/BIKE/HQC wrappers.
- **Benchmarking improvements**: Add memory profiling, CPU profile export, comparison reports.
- **SCA tooling**: Add CPA (Correlation Power Analysis) support to `pkg/sca/`, or add trace file importers for ChipWhisperer/Riscure formats.
- **Documentation**: Improve `docs/GUIDE.md`, add examples, fix typos.

### Development workflow

```bash
# Run tests
make test

# Run benchmarks
make bench

# Generate vectors and validate
make vectors
./pqc-testkit kat -a ml-kem -v vectors/

# Lint HDL (requires Verilator)
make lint
```

## Code conventions

- Go: proper godoc on all exported symbols
- SystemVerilog: vendor-agnostic in `hdl/core/`, vendor-specific in `hdl/xilinx/` or `hdl/intel/`
- Tests: `_test.go` files alongside source
- No `%` operator in synthesizable HDL - use Barrett reduction from `mod_reduce.sv`

## Submitting a PR

- Keep PRs focused: one board, one core, or one feature per PR
- Include test results in the PR description
- If adding HDL: include synthesis results (LUTs, FFs, BRAMs, clock frequency)
- If adding a board: include a photo or link to the board product page
