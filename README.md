# pqc-testkit

An open-source toolkit for testing and validating post-quantum cryptography implementations on FPGA hardware.

Brings together software PQC validation, FPGA-in-the-loop testing, HDL building blocks, side-channel analysis, and a curated research bibliography in one place. Built for hardware engineers, security researchers, and anyone working on PQC hardware adoption.

## What's in the box

| Layer | What you get |
|-------|-------------|
| **CLI** | `bench`, `kat`, `fpga`, `gen-vectors`, `gen-twiddle` - one binary, all operations |
| **Algorithms** | ML-KEM (FIPS 203), ML-DSA (FIPS 204), SLH-DSA (FIPS 205) - software reference via Go stdlib + circl |
| **FPGA transports** | PCIe XDMA, AXI UIO (Zynq), UART, simulation mode - one `Device` interface |
| **HDL cores** | 15 SystemVerilog modules: Keccak-f[1600], NTT engine with Barrett reduction, AXI-Lite CSR, UART bridge, ML-KEM building blocks (CBD, compress, encode) |
| **Side-channel** | TVLA test vector generation + Welch's t-test for leakage detection |
| **Board support** | Arty A7, Nexys A7, ZCU102, Alveo U250 (Xilinx) + DE10-Nano, DE10-Agilex, Stratix 10 (Intel) |
| **Research** | 94-paper bibliography as queryable Go data - ML-KEM, ML-DSA, SLH-DSA, NTT, side-channel, industry |
| **CI** | GitHub Actions: Go tests + HDL lint via Verilator |

## Get started (2 minutes)

```bash
git clone https://github.com/akhilesharora/pqc-testkit.git
cd pqc-testkit
make test        # 40 tests, all algorithms, all pass
make bench       # ML-KEM ~93us keygen, ML-DSA ~375us sign, SLH-DSA ~15ms sign
make simulate   # Simulate full FPGA validation flow, no hardware needed
make vectors     # Generate KAT test vectors from software reference
make twiddle     # Generate NTT twiddle factors for FPGA synthesis
```

## Validate on real hardware

The ML-KEM core implements ML-KEM-768 only, so its resource usage compares
fairly with single-level designs; any other SEC_LEVEL fails with
ERROR_CODE 1. Regenerate the microcode ROM with `make ucode`.

```bash
# 1. Synthesize for your board
vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem

# 2. Program the FPGA
vivado -mode batch -source scripts/program.tcl -tclargs path/to/bitstream.bit

# 3. Run validation (KATs at the levels built into the bitstream, default 768)
./pqc-testkit fpga -T uart -d /dev/ttyUSB1 --levels 768

# Output:
# [1/4] Connecting to FPGA... OK (uart)
# [2/4] Reading core identification... OK
#        Algorithm:      ML-KEM (ID=1)
#        Security Level: 768
#        Core Version:   1.0.0
# [3/4] Register read/write test... OK
# [4/4] Operation cycle test... OK (42000 hardware cycles)
```

No hardware? Use `--transport sim` to run the full flow on any machine.

## Simulate HDL (no board needed)

```bash
# Lint all cores
make lint

# Run cocotb testbenches (requires Verilator + cocotb)
cd testbench/keccak && make SIM=verilator
cd testbench/ntt && make SIM=verilator
```

## Supported algorithms

| Algorithm | Standard | Type | Security levels | Software | HDL building blocks |
|-----------|----------|------|-----------------|----------|-------------------|
| ML-KEM | FIPS 203 | Key encapsulation | 768, 1024 | Go stdlib `crypto/mlkem` | Keccak, NTT (q=3329), CBD, compress, encode |
| ML-DSA | FIPS 204 | Digital signatures | 44, 65, 87 | cloudflare/circl | Keccak, NTT (q=8380417) |
| SLH-DSA | FIPS 205 | Hash-based signatures | 128s/f, 192s/f, 256s/f | cloudflare/circl | Keccak |

## HDL modules

All vendor-agnostic SystemVerilog in `hdl/core/`. No Xilinx or Intel primitives.

| Module | Lines | Purpose |
|--------|-------|---------|
| `keccak_f1600.sv` | 190 | 24-round Keccak-f[1600] permutation, 1 round/cycle |
| `keccak_absorb_squeeze.sv` | 150 | Sponge construction for SHA3/SHAKE with configurable rate |
| `ntt_engine.sv` | 260 | 256-point NTT/INTT with Barrett reduction, twiddle ROM |
| `ntt_butterfly.sv` | 140 | Parameterized CT/GS butterfly, 2-cycle pipelined |
| `mod_reduce.sv` | 100 | Barrett reduction for q=3329 and q=8380417, mod_add, mod_sub |
| `pqc_axi_csr.sv` | 160 | AXI4-Lite CSR slave implementing the standard register map |
| `pqc_data_buffer.sv` | 50 | Dual-port BRAM for host/datapath data exchange |
| `pqc_mlkem_top.sv` | 280 | ML-KEM accelerator top-level integrating all cores |
| `mlkem_cbd.sv` | 60 | Centered Binomial Distribution sampler (eta=2/3) |
| `mlkem_compress.sv` | 60 | Compress_d / Decompress_d for ciphertext encoding |
| `mlkem_encode.sv` | 90 | ByteEncode / ByteDecode streaming serialization |
| `uart_rx.sv` | 80 | 8N1 UART receiver with 16x oversampling |
| `uart_tx.sv` | 60 | 8N1 UART transmitter |
| `uart_axi_bridge.sv` | 200 | Protocol bridge: pqc-testkit UART frames to AXI-Lite transactions |
| `arty_a7_top.sv` | 170 | Arty A7 board wrapper with constraints |

## FPGA register map

Any PQC core that implements this register map works with pqc-testkit:

```
0x00 CTRL        RW  bit 0=start, bit 1=reset
0x04 STATUS      RO  bit 0=busy, bit 1=done, bit 2=error
0x08 ALG_ID      RO  1=ML-KEM, 2=ML-DSA, 3=SLH-DSA
0x0C SEC_LEVEL   RW  security level
0x10 OP_MODE     RW  0=keygen, 1=encaps/sign, 2=decaps/verify
0x14 CYCLE_COUNT RO  latches on done
0x18 VERSION     RO  packed major.minor.patch
```

Full specification in `pkg/fpga/doc.go`. AXI-Lite implementation in `hdl/core/pqc_axi_csr.sv`.

## Side-channel testing

```go
// Generate TVLA fixed-vs-random test patterns
cfg := sca.TVLAConfig{NumTraces: 10000, KeySize: 32, MessageSize: 32}
vectors, _ := sca.GenerateTVLA(cfg)

// After capturing power traces from your FPGA...
result, _ := sca.WelchTTest(fixedTraces, randomTraces)
// result.Leakage == true if |t| > 4.5 at any sample point
```

## Research bibliography

94 papers indexed as structured Go data in `docs/papers/index.go`:

```go
// Query papers by category
for _, p := range papers.Index {
    if p.Category == "Side-Channel Attack" && p.Year >= 2024 {
        fmt.Printf("%s (%d) - %s\n", p.Title, p.Year, p.URL)
    }
}
```

Categories: ML-KEM implementations, ML-DSA implementations, SLH-DSA implementations, NTT architectures, unified multi-algorithm, side-channel attacks (15 papers), side-channel defenses (11 papers), HW/SW co-design, code-based PQC, frameworks, surveys, standards.

## Contributing

Three paths depending on what you have:

1. **I have an FPGA board** - synthesize, program, run `./pqc-testkit fpga`, report results
2. **I have a PQC hardware core** - wrap it behind `pqc_axi_csr.sv`, validate with the test kit
3. **I don't have hardware** - add ACVP JSON support, new algorithms, better benchmarking, SCA tooling

See [CONTRIBUTING.md](CONTRIBUTING.md) for details. Issue templates for [board support](.github/ISSUE_TEMPLATE/board-support.md) and [algorithm cores](.github/ISSUE_TEMPLATE/algorithm-core.md).

## Full documentation

- **[docs/GUIDE.md](docs/GUIDE.md)** - end-to-end guide from `git clone` to validated hardware
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - how to add boards, cores, or software features
- **[CLAUDE.md](CLAUDE.md)** - project context for AI-assisted development

## License

MIT
