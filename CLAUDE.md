# ML-KEM-Testkit

## Project overview

ML-KEM-768 (FIPS 203) hardware core for Xilinx Arty boards plus the host tool chain around it: NIST ACVP functional testing (board over UART, or the RTL in Verilator), reproducible test vectors, oscilloscope trace acquisition and CPA/TVLA side-channel analysis, all reachable from a local web GUI.

## Architecture

- **HDL** (`hdl/core/`, vendor-agnostic SystemVerilog): `pqc_mlkem_top` (CSR + 8 KB data buffer + microcoded datapath: Keccak, NTT, BaseMul, CBD, (de)compress, pack/unpack), `uart_axi_bridge` (host protocol to AXI-Lite), `axil_cdc` (100/125 MHz bridge clock to the 206 MHz core clock), `status_leds` (green + RGB LEDs, frozen while the core is busy), and `mlkem_uart_system`, which wires all of these plus `trig_out` and is the one block every board top instantiates.
- **Board tops** (`hdl/xilinx/`): clocking (MMCM), reset synchronisers and pins only: `arty_a7`, `arty_z7` (pure PL, external USB-UART on Pmod JA), `arty_z7_ps` (on-board USB via the Zynq PS; `sw/arty_z7_uart_fwd` forwards bytes PS UART0 <-> PS UART1/EMIO), `sakura_x` (SAKURA-X / SASEBO-GIII Kintex-7 XC7K160T; its own top, not `mlkem_uart_system`, see the SAKURA-X section below). `trig_out = STATUS.busy` is the scope trigger.
- **Timing at 206 MHz**: the core-clock critical paths ran from the read FIFO (`mlkem_word_fifo`) into the sponge / unpack; the FIFO now registers its bank addresses and fill flags (cycle-exact). Any RTL change must keep ACVP cycle counts identical (`make acvp-sim`, compare `data/acvp/*/results.csv`) unless a cycle change is intended.
- **Host** (`cpa_toolkit/`, Python, flat modules imported by name): `uart_target` (protocol driver, `Target` step API), `sakura_x` (SAKURA-X FIFO transport, `sakura-x:` ports), `connection` (non-blocking connect/disconnect, link monitor), `mlkem768` / `mlkem_ref` (FIPS 203 reference), `cpa` (`cpa.py`: the simple coefficient CPA; its hypotheses live in `models/` (one module per family, `models/HW.py`, auto-loaded by `models/__init__.py`), worded as an evaluation / analysis everywhere (code, GUI, docs); 2026-10-20: two hypotheses `hw_product_mod` = HW(u·g mod q) and `hw_product_raw` = HW(u·g), correlated with every sample of the BMUL window; top-10 + stability vote over 3/5/7/9-sample windows around the POI; the peak position is compared with the cycle the RTL schedule predicts; the true key only RANKS the guesses, hypotheses are built from the public ciphertext alone -- replaces the removed `models/` catalogue and row-aligned analysis, see docs/LEAKAGE_MODELS.md), `mlkem_pipeline` (bit-exact software twin of the two Barrett multipliers, checked against the RTL by `scripts/sim/pipeline_check.py`), `pc_oracle` (plaintext-checking oracle key recovery: chosen ciphertexts that read one time-domain secret coefficient per query from the message-dependent re-encryption; a `pc-oracle` testvectors strategy captures it and `decode_campaign` recovers the key), `testvectors`, `acquire_cpa` + `hdf5_trace_store` (campaigns store `record_start_s`: the time of sample 0 relative to the trigger, so BMUL windows are placed by time; `TRIGGER_SLACK_CYCLES` is in core cycles), `tvla_analysis`, `scope` (VISA: `LeCroyScope` for Teledyne LeCroy MAUI over VICP/VXI-11, `VisaScope` for SCPI; `open_scope()` picks by `SCOPE_DIALECT`, transport by `SCOPE_TRANSPORT`), `web_gui` + `web_static/` (stdlib HTTP server, vanilla JS, vendored Plotly).
- **Trace parsing** (`cpa_toolkit/trace_parse.py`, 2026-10-23): the scope records the trig_out marker channel
  (`SCOPE_MARKER_CHANNEL`, default C4) next to the signal; campaigns store it as `/marker` and the per-trace Decaps
  start (`/parse`: rise, fall, ok + JSON summary; other H5 files: `<file>.parse.npz`). BMUL windows are placed from
  each trace's own start (measured clock when the falling edge is in the record). Without a parse the GUI draws NO
  windows and `cpa.Timing` falls back to "trigger = Decaps start", flagged as not parsed. The GUI's Parse & View page
  (`web_static/traces.js`, `/api/h5/*`) opens any trace H5, including per-channel capture folders
  (`C2_traces.h5` + `C4_traces.h5`, see `sakura_trace_example/`).
- **Scripts**: `scripts/vivado/` (build/program Tcl), `scripts/board/` (Z7 PS boot over JTAG; SAKURA-X EEPROM tool `sakura_x_eeprom.sh` and CLI `sakura_x_cli.py`), `scripts/test/acvp_test.py`, `scripts/gen/gen_mlkem_ucode.py` (microcode + zeta ROMs), `scripts/sim/` (analyses of the RTL simulation: `bmul_timing.py` measures the Decaps windows that go into `cpa_toolkit/config.py`).

## Key design decisions

- One host protocol for every board (`docs/UART_PROTOCOL.md`): CRC-32 frames, commands 0x01-0x04 (register / buffer access) and 0x05 Run (write CTRL.start, reply only after the core is idle with STATUS, CYCLE_COUNT, ERROR_CODE and the result). The link is silent while the core computes. 0x05 is the only way operations are started (no STATUS polling anywhere in the host code); `Target.probe_run()` detects bitstreams without 0x05 and operations on them raise `BitstreamError`. `trig_out` rises on the core clock edge an operation starts and stays high for exactly CYCLE_COUNT cycles (checked in `testbench/cdc/tb_bridge.sv`; on the bench: GUI Acquisition -> Check trigger).
- CSR register map (`hdl/core/pqc_axi_csr.sv`, `uart_target.REG`) is fixed; board-specific code is only in the tops and the transport.
- Test vectors are a deterministic function of a 64-byte seed; every trace is checked against its precomputed shared secret K and CYCLE_COUNT.
- Campaign files are HDF5 (`/traces`, `/intermediates/ct`, `/intermediates/k_expected`, metadata incl. the vector-set recipe), resumable after a stop or link loss.
- Arty Z7 changes must not affect the Arty A7 build; SAKURA-X changes must not affect either Arty build (own top, XDC, Tcl and project directory).

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

1. The CPA hypotheses cover one register's Hamming weight (the multiplier's raw 24-bit product or its reduced 12-bit output). The chip toggles ~1400 flops per cycle and the two multipliers finish a coefficient pair in the same cycle, so the partner coefficient is algorithmic noise; there is no HD / previous-value hypothesis (the old `hd_*` / `pipe_*` models were removed on 2026-10-20, history in git).
2. The only cocotb testbench is Keccak (`testbench/keccak`); everything else is Icarus / Verilator (`testbench/iverilog`, `units`, `sponge`, `cdc`, `core_sim`).
3. Throughput at 115200 baud is bounded by the 1088-byte ciphertext upload (~94 ms/trace); 1 Mbaud needs `BAUD_RATE` in the tops and the Z7 forwarder divisors changed together.

## Gotchas

- The `structured` testvectors strategy was removed on 2026-10-23 (it isolated nothing after the
  core's NTT). Old sets and campaigns that carry it still load. `cpa` flags coefficients a
  campaign cannot discriminate (`informative`) instead of scoring them.
- GUI text (index.html, app.js, server messages shown in the GUI) follows ASD-STE100: short
  sentences, one instruction each, approved words. Keep new text in that style; docs/GUI_GUIDE.md
  is the Chinese guide written to the same rules.
- GUI lists: moving to the trash does not ask (the toast offers Undo); permanent deletion asks.
  Ticked rows drive the action buttons and Del/Esc/Ctrl+A shortcuts.

## Conventions

- MIT license; docs in `docs/` are written in Chinese, code comments and docstrings in English.
- Python: docstrings on public functions, tests in `tests/` (`unittest`).
- HDL: vendor-agnostic in `hdl/core/`, board-specific in `hdl/xilinx/<board>/`; simulation in `testbench/`.


## SAKURA-X

- Top `hdl/xilinx/sakura_x/sakura_x_top.sv` + `sakura_x_bridge.sv`: the pinned upstream Spartan-6
  shell controller (`third_party/sakura_x_shell/`, vendored snapshot, prebuilt bit) drives a
  source-synchronous parallel handshake; 50 MHz bus clock deskewed by MMCM + BUFG, IOB registers on
  all bus I/O, core at 206.25 MHz through `axil_cdc`. `make sim-sakura` checks the bridge against the
  unmodified upstream `command_control.v` / `fifo.v`.
- Build `scripts/vivado/build_sakura_x.tcl` (blocks the bitstream on negative WNS/WHS); program the
  Kintex with `program_sakura_x.tcl`, the Spartan with xc3sprog (docs/SAKURA_X.md).
- Host transport `cpa_toolkit/sakura_x.py`, port `sakura-x:<channel-A device>` (`sakura-shell:` from
  older saved settings is still accepted). Channel B is paired by USB parent / serial and used only
  for the RTS_N reset; every connect starts with it and identifies the Spartan controller (shell /
  AIST factory / none) before touching the Kintex.
- FT2232H channel A must be 245 FIFO (the cause of every early "no reply" failure); fix it with
  `bash scripts/board/sakura_x_eeprom.sh` (93C46 = 128 bytes; pyftdi must be opened with that size).
- Commands RESET, LOAD_DK, LOAD_CT, START, READ_SS; Decaps only. START is a pending read: the bridge
  writes CTRL.start, waits on synchronized busy (no polling), then answers after a quiet tail.
  Bus words are big endian; core buffer bytes keep their little-endian word layout; no CRC.
- The old AIST local-bus stack (sakura_lbus, factory-controller bridge, firmware/sakura_x) was
  removed on 2026-10-23; history is in git.
- Board status: after the EEPROM fix the GUI connects and runs on the Linux bench (user, 2026-10-23).
