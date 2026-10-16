.PHONY: help setup gui test vectors ucode sim-iverilog sim-link sim-bridge sim-system sim-cdc sim-units \
	acvp-sim acvp-uart check lint regress clean

# Python of the project environment (pyproject.toml / uv.lock).
PY ?= uv run python

# ML-KEM levels in the microcode ROM (the core supports 768 only).
MLKEM_LEVELS ?= 768

help:
	@echo "make setup         install the Python environment (uv sync; + --extra scope for a real scope)"
	@echo "make gui           web GUI on http://127.0.0.1:8765 (cpa_toolkit/web_gui.py)"
	@echo "make test          Python unit tests (FIPS 203 reference, test vectors, UART host driver)"
	@echo "make vectors       one CPA test-vector set into data/vectors/ (N=1000 STRATEGY=valid)"
	@echo "make check PORT=   board link self-test over UART (no scope needed)"
	@echo "make acvp-uart PORT=  NIST ACVP vectors on the board;  make acvp-sim: same on the RTL"
	@echo "make sim-link      UART link + 0x05 Run (Icarus);  make sim-bridge: host protocol on the real core"
	@echo "make sim-system    whole board system over a serial line, incl. trigger and LEDs"
	@echo "make regress       all simulation and unit tests"
	@echo "make ucode         regenerate the microcode / zeta ROMs"
	@echo "Protocol: docs/UART_PROTOCOL.md   ACVP: docs/ACVP_TEST.md   CPA/TVLA: cpa_toolkit/README.md"

setup:
	uv sync

gui:
	$(PY) cpa_toolkit/web_gui.py

test:
	$(PY) -m unittest discover -s tests -v

vectors:
	$(PY) cpa_toolkit/testvectors.py gen --mode cpa -n $(or $(N),1000) --strategy $(or $(STRATEGY),valid)

check:
	@test -n "$(PORT)" || (echo "usage: make check PORT=<serial port>"; exit 2)
	$(PY) cpa_toolkit/acquire_cpa.py check -p $(PORT)

# Regenerate the ML-KEM microcode and zeta ROMs.
ucode:
	$(PY) scripts/gen/gen_mlkem_ucode.py --levels $(MLKEM_LEVELS)

# Icarus Verilog: UART byte loopback + NTT, VCDs in build/sim/.
sim-iverilog:
	$(MAKE) -C testbench/iverilog

# Full UART host link incl. 0x05 Run against a core model (Icarus, 1 Mbaud for speed).
sim-link:
	$(MAKE) -C testbench/iverilog uart-link BAUD=1000000

# Host protocol -> uart_axi_bridge -> axil_cdc -> real core, ACVP Decaps via 0x05 (Verilator).
sim-bridge:
	$(MAKE) -C testbench/cdc bridge

# The whole board system (mlkem_uart_system) over a serial line: Decaps, trigger, LEDs (Verilator).
sim-system:
	$(MAKE) -C testbench/cdc system

# axil_cdc + real core: registers, KeyGen and Decaps through the crossing (Verilator).
sim-cdc:
	$(MAKE) -C testbench/cdc

# Unit benches (modmul, pack/unpack, FIFO, Keccak) and the sponge.
sim-units:
	$(MAKE) -C testbench/units all
	$(MAKE) -C testbench/sponge

# NIST ACVP ML-KEM-768 vectors on the RTL in Verilator, report in data/acvp/ (docs/ACVP_TEST.md).
acvp-sim:
	$(PY) scripts/test/acvp_test.py sim

# The same vectors on the board over UART:  make acvp-uart PORT=/dev/cu.usbserial-XXXX1
acvp-uart:
	@test -n "$(PORT)" || (echo "usage: make acvp-uart PORT=<serial port>"; exit 2)
	$(PY) scripts/test/acvp_test.py uart -p $(PORT)

lint:
	verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME hdl/core/uart_axi_bridge.sv
	verilator --lint-only -Wall -Wno-fatal hdl/core/uart_rx.sv
	verilator --lint-only -Wall -Wno-fatal hdl/core/uart_tx.sv
	verilator --lint-only -Wall -Wno-fatal --top-module keccak_f1600 hdl/core/keccak_f1600.sv hdl/core/keccak_round.sv
	verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME hdl/core/status_leds.sv
	verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME hdl/core/mlkem_word_fifo.sv

regress: test sim-iverilog sim-link sim-units sim-cdc sim-bridge sim-system acvp-sim

# Simulation outputs only. build/vivado/ (Vivado projects) and data/ are never touched.
clean:
	rm -rf build/sim build/cdc testbench/core_sim/obj_dir
