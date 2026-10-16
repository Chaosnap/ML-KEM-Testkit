# ML-KEM-Testkit

## Project overview

ML-KEM-768 (FIPS 203) hardware core for Xilinx Arty boards plus the host tool chain around it: NIST ACVP functional testing (board over UART, or the RTL in Verilator), reproducible test vectors, oscilloscope trace acquisition and CPA/TVLA side-channel analysis, all reachable from a local web GUI.

## Architecture

- **HDL** (`hdl/core/`, vendor-agnostic SystemVerilog): `pqc_mlkem_top` (CSR + 8 KB data buffer + microcoded datapath: Keccak, NTT, BaseMul, CBD, (de)compress, pack/unpack), `uart_axi_bridge` (host protocol to AXI-Lite), `axil_cdc` (100/125 MHz bridge clock to the 206 MHz core clock), `status_leds` (green + RGB LEDs, frozen while the core is busy), and `mlkem_uart_system`, which wires all of these plus `trig_out` and is the one block every board top instantiates.
- **Board tops** (`hdl/xilinx/`): clocking (MMCM), reset synchronisers and pins only: `arty_a7`, `arty_z7` (pure PL, external USB-UART on Pmod JA), `arty_z7_ps` (on-board USB via the Zynq PS; `sw/arty_z7_uart_fwd` forwards bytes PS UART0 <-> PS UART1/EMIO). `trig_out = STATUS.busy` is the scope trigger.
- **Timing at 206 MHz**: the core-clock critical paths ran from the read FIFO (`mlkem_word_fifo`) into the sponge / unpack; the FIFO now registers its bank addresses and fill flags (cycle-exact). Any RTL change must keep ACVP cycle counts identical (`make acvp-sim`, compare `data/acvp/*/results.csv`) unless a cycle change is intended.
- **Host** (`cpa_toolkit/`, Python, flat modules imported by name): `uart_target` (protocol driver, `Target` step API), `connection` (non-blocking connect/disconnect, link monitor), `mlkem768` / `mlkem_ref` (FIPS 203 reference + leakage models), `testvectors`, `acquire_cpa` + `hdf5_trace_store`, `cpa_attack`, `tvla_analysis`, `scope` (VISA), `web_gui` + `web_static/` (stdlib HTTP server, vanilla JS, vendored Plotly).
- **Scripts**: `scripts/vivado/` (build/program Tcl), `scripts/board/` (Z7 PS boot over JTAG), `scripts/test/acvp_test.py`, `scripts/gen/gen_mlkem_ucode.py` (microcode + zeta ROMs).

## Key design decisions

- One host protocol for every board (`docs/UART_PROTOCOL.md`): CRC-32 frames, commands 0x01-0x04 (register / buffer access) and 0x05 Run (write CTRL.start, reply only after the core is idle with STATUS, CYCLE_COUNT, ERROR_CODE and the result). The link is silent while the core computes. 0x05 is the only way operations are started (no STATUS polling anywhere in the host code); `Target.probe_run()` detects bitstreams without 0x05 and operations on them raise `BitstreamError`. `trig_out` rises on the core clock edge an operation starts and stays high for exactly CYCLE_COUNT cycles (checked in `testbench/cdc/tb_bridge.sv`; on the bench: GUI Acquisition -> Check trigger).
- CSR register map (`hdl/core/pqc_axi_csr.sv`, `uart_target.REG`) is fixed; board-specific code is only in the tops and the transport.
- Test vectors are a deterministic function of a 64-byte seed; every trace is checked against its precomputed shared secret K and CYCLE_COUNT.
- Campaign files are HDF5 (`/traces`, `/intermediates/ct`, `/intermediates/k_expected`, metadata incl. the vector-set recipe), resumable after a stop or link loss.
- Arty Z7 changes must not affect the Arty A7 build.

## Build and test

```bash
uv sync                    # + --extra scope for a real oscilloscope
make test                  # Python unit tests (tests/)
make sim-link sim-bridge sim-system   # UART link / host protocol / whole board system on the real core
make regress               # all simulations + unit tests
make gui                   # web GUI on http://127.0.0.1:8765
```

Vivado builds: `vivado -mode batch -source scripts/vivado/build_arty_a7.tcl -tclargs arty-a7-100t mlkem` (see README.md for Z7). After any RTL change the bitstreams must be rebuilt.

## Data locations

- `build/` (Vivado projects, simulation outputs) and `data/` (vectors, traces, analysis, ACVP results) are generated and git-ignored. `make clean` removes simulation outputs only, never `build/vivado/`.

## Known gaps

1. The only cocotb testbench is Keccak (`testbench/keccak`); everything else is Icarus / Verilator (`testbench/iverilog`, `units`, `sponge`, `cdc`, `core_sim`).
2. NTT/INTT cycle windows in `cpa_toolkit/config.py` are not measured yet (only BMUL windows are).
3. `model_registry.py` plugins are display/self-test only; `cpa_attack.py` always uses `mlkem_ref`.
4. Throughput at 115200 baud is bounded by the 1088-byte ciphertext upload (~94 ms/trace); 1 Mbaud needs `BAUD_RATE` in the tops and the Z7 forwarder divisors changed together.

## Conventions

- MIT license; docs in `docs/` are written in Chinese, code comments and docstrings in English.
- Python: docstrings on public functions, tests in `tests/` (`unittest`).
- HDL: vendor-agnostic in `hdl/core/`, board-specific in `hdl/xilinx/<board>/`; simulation in `testbench/`.
