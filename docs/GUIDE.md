# pqc-testkit: End-to-End Guide

Everything you need to go from `git clone` to validated PQC hardware.

## Part 1: Software validation (no hardware needed)

### Setup

```bash
git clone https://github.com/akhilesharora/pqc-testkit.git
cd pqc-testkit
go mod tidy
go build ./cmd/pqc-testkit
```

### Run tests

```bash
# Full test suite: validates algorithms, KAT parser, TVLA, FPGA simulation device
go test ./... -v

# What this tests:
# - ML-KEM-768/1024: keygen, encapsulate, decapsulate, shared secret match
# - ML-DSA-44/65/87: keygen, sign, verify, deterministic signing, wrong-key rejection
# - SLH-DSA: keygen, sign, verify (SHA2-128f), wrong-message rejection
# - KAT parser: .rsp file parsing, field name mapping (pk/ek, sk/dk)
# - TVLA: test vector generation, Welch's t-test leakage detection
# - FPGA sim: device identification, register read/write, operation cycle
```

### Benchmark

```bash
# ML-KEM (Kyber) - key encapsulation
./pqc-testkit bench -a ml-kem -n 1000
# Output: keygen ~241us, encaps ~282us, decaps ~521us per operation

# ML-DSA (Dilithium) - digital signatures
./pqc-testkit bench -a ml-dsa -n 100
# Output: sign ~375us, verify ~170us per operation

# SLH-DSA (SPHINCS+) - hash-based signatures
./pqc-testkit bench -a slh-dsa -n 5
# Output: sign ~15ms (this is why FPGA acceleration matters)
```

### Simulate FPGA flow (no board needed)

```bash
# The simulation transport simulates a full ML-KEM-768 accelerator core.
# This runs the exact same validation sequence as real hardware.
./pqc-testkit fpga --transport sim

# Output:
# [1/4] Connecting to FPGA... OK (sim)
# [2/4] Reading core identification... OK
#        Algorithm:      ML-KEM (ID=1)
#        Security Level: 768
#        Core Version:   1.0.0
# [3/4] Register read/write test... OK
# [4/4] Operation cycle test... OK (42000 hardware cycles)
```

This validates the entire host-side flow: transport open, register read, write/readback verify, operation start, status poll, cycle count read.

## Part 2: HDL simulation (no board needed)

### Prerequisites

```bash
# Install Verilator (open-source Verilog simulator)
# Ubuntu/Debian
sudo apt-get install verilator

# macOS
brew install verilator

# Verify
verilator --version
```

### Lint all HDL cores

```bash
# This catches syntax errors, width mismatches, unused signals, latch inference.
# Fix all warnings before synthesis.

# Core building blocks
verilator --lint-only -Wall hdl/core/keccak_f1600.sv
verilator --lint-only -Wall hdl/core/uart_rx.sv
verilator --lint-only -Wall hdl/core/uart_tx.sv

# NTT engine (uses modulus operator - Verilator will warn, this is expected)
verilator --lint-only -Wall hdl/core/ntt_butterfly.sv
verilator --lint-only -Wall hdl/core/ntt_engine.sv

# Full ML-KEM accelerator (all dependencies)
verilator --lint-only -Wall \
    hdl/core/pqc_mlkem_top.sv \
    hdl/core/pqc_axi_csr.sv \
    hdl/core/pqc_data_buffer.sv \
    hdl/core/keccak_f1600.sv \
    hdl/core/ntt_engine.sv

# Board-level wrapper (Arty A7)
verilator --lint-only -Wall \
    hdl/xilinx/arty_a7/arty_a7_top.sv \
    hdl/core/pqc_mlkem_top.sv \
    hdl/core/pqc_axi_csr.sv \
    hdl/core/pqc_data_buffer.sv \
    hdl/core/keccak_f1600.sv \
    hdl/core/ntt_engine.sv \
    hdl/core/uart_rx.sv \
    hdl/core/uart_tx.sv \
    hdl/core/uart_axi_bridge.sv
```

## Part 3: FPGA hardware (real board)

### What you need

| Board | Transport | Cable | Cost |
|-------|-----------|-------|------|
| Arty A7-35T | UART | USB (on-board) | ~$130 |
| ZCU102 | AXI (on-chip) | None | ~$3K |
| Alveo U250 | PCIe Gen3 x16 | PCIe slot | ~$6K |

Arty A7 is the recommended starting point. Everything below uses it as the example.

### Step 1: Synthesize

```bash
# Requires Vivado 2023.1 or later (free for Artix-7 via WebPACK license).

# Build the bitstream (runs synthesis, implementation, bitstream generation)
vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem

# Output:
# build/vivado/arty-a7-35t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit
# build/vivado/arty-a7-35t_mlkem/utilization_impl.rpt  (LUTs, FFs, BRAMs)
# build/vivado/arty-a7-35t_mlkem/timing_impl.rpt        (clock frequency)
# build/vivado/arty-a7-35t_mlkem/power.rpt               (power estimate)
```

### Step 2: Program the board

```bash
# Plug in the Arty A7 via USB.
# Open Vivado Hardware Manager (GUI) and program the device.
# Or use the command line:
vivado -mode batch -source scripts/program.tcl -tclargs \
    build/vivado/arty-a7-35t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit

# LEDs should show:
#   LED3 blinking: heartbeat (core is alive)
#   LED0-2 off: idle
```

### Step 3: Validate

```bash
# Find the serial port
ls /dev/ttyUSB*
# Usually /dev/ttyUSB1 on Linux (USB0 is JTAG, USB1 is UART)

# Run the validation
./pqc-testkit fpga -T uart -d /dev/ttyUSB1

# Expected output:
# [1/4] Connecting to FPGA... OK (uart)
# [2/4] Reading core identification... OK
#        Algorithm:      ML-KEM (ID=1)
#        Security Level: 768
#        Core Version:   1.0.0
#        Transport:      uart
# [3/4] Register read/write test... OK
# [4/4] Operation cycle test... OK (XXXXX hardware cycles)

# LEDs during operation:
#   LED0 on:  core is processing
#   LED1 on:  operation complete
#   LED2 on:  error (check error code)
```

### Step 4: Compare with software baseline

The cycle count from step 3 tells you your hardware performance. Compare:

```
ML-KEM-768 software (bench):  ~241us keygen, ~521us decaps
ML-KEM-768 on Arty A7 @ 100MHz:
  42,000 cycles = 420us  (comparable to software on modern CPU)
  10,000 cycles = 100us  (2.4x faster with parallel NTT)
   5,000 cycles =  50us  (5x faster with 4-butterfly NTT)
```

If your cycle count is higher than software, your NTT needs more parallelism or your FSM has unnecessary stalls.

## Troubleshooting

| What you see | What it means | What to do |
|-------------|--------------|-----------|
| `no such file or directory` | Wrong device path | Run `ls /dev/ttyUSB*`, try USB1 instead of USB0 |
| `permission denied` | Need permissions | `sudo chmod 666 /dev/ttyUSB1` or add udev rule |
| `Algorithm: unknown(0)` | CSR not implemented | Check ALG_ID register is at offset 0x08 |
| `register readback mismatch` | SEC_LEVEL not writable | Verify offset 0x0C is RW in your RTL |
| `timeout waiting for accelerator` | FSM stuck | Check STATUS.done bit is set when operation completes |
| `accelerator error: code=0x0042` | Core error | Check ERROR_CODE register for your core's error definitions |
| `CRC mismatch` | UART noise | Verify baud rate matches (default 115200), try shorter USB cable |
| LED3 not blinking | FPGA not programmed | Re-program via Vivado hardware manager |
| LED2 on | Core in error state | Reset via `./pqc-testkit fpga -T uart -d /dev/ttyUSB1` (sends reset command) |

## Architecture reference

```
Host computer                       FPGA board
--------------                      ----------
                                    ┌─────────────────────────┐
┌──────────────┐    USB cable       │  arty_a7_top.sv         │
│ pqc-testkit  │◄──────────────────►│    uart_rx/tx           │
│              │    /dev/ttyUSB1    │    uart_axi_bridge      │
│ fpga command │                    │    pqc_mlkem_top        │
│   opens uart │                    │      pqc_axi_csr (CSR)  │
│   reads regs │                    │      pqc_data_buffer    │
│   starts op  │                    │      keccak_f1600       │
│   polls done │                    │      ntt_engine         │
│   reads data │                    │    LED status           │
└──────────────┘                    └─────────────────────────┘
```
