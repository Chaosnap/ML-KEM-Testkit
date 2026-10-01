# Icarus Verilog testbenches for the UART link and the ML-KEM NTT core.
#
#   make -C testbench/iverilog            # run both, write VCDs
#   make -C testbench/iverilog uart       # one byte tx -> rx, build/sim/tb_uart.vcd
#   make -C testbench/iverilog ntt        # build/sim/tb_ntt.vcd
#   make -C testbench/iverilog uart-link  # full host protocol via uart_axi_bridge
#                                         # (long: ~15 ms simulated, 77 MB VCD)
#   make -C testbench/iverilog uart-link BAUD=1000000   # faster link run
#
# See docs/IVERILOG_SIM_GUIDE.md for how to read the waveforms.

ROOT  := $(abspath ../..)
RTL   := $(ROOT)/hdl/core
OUT   := $(ROOT)/build/sim
TB    := $(abspath .)

IVERILOG ?= iverilog
VVP      ?= vvp
IVFLAGS  := -g2012 -Wall -Wno-timescale -Wno-implicit-dimensions -Wno-sensitivity-entire-array -Wno-sensitivity-entire-vector

BAUD ?= 115200

UART_SRC := $(TB)/tb_uart.sv $(RTL)/uart_rx.sv $(RTL)/uart_tx.sv
LINK_SRC := $(TB)/tb_uart_link.sv $(RTL)/uart_rx.sv $(RTL)/uart_tx.sv $(RTL)/uart_axi_bridge.sv
NTT_SRC  := $(TB)/tb_ntt.sv $(RTL)/mlkem_poly_alu.sv $(RTL)/mlkem_polyram.sv \
            $(RTL)/mlkem_modmul.sv $(RTL)/mlkem_zetas.sv

.PHONY: all uart uart-link ntt clean

all: uart ntt

$(OUT):
	mkdir -p $(OUT)

uart: | $(OUT)
	$(IVERILOG) $(IVFLAGS) -s tb_uart -o $(OUT)/tb_uart.vvp $(UART_SRC)
	cd $(OUT) && $(VVP) -n tb_uart.vvp | tee tb_uart.log
	@grep -q "TB_UART PASS" $(OUT)/tb_uart.log

uart-link: | $(OUT)
	$(IVERILOG) $(IVFLAGS) -P tb_uart_link.BAUD_RATE=$(BAUD) -s tb_uart_link -o $(OUT)/tb_uart_link.vvp $(LINK_SRC)
	cd $(OUT) && $(VVP) -n tb_uart_link.vvp | tee tb_uart_link.log
	@grep -q "TB_UART_LINK PASS" $(OUT)/tb_uart_link.log

ntt: | $(OUT)
	$(IVERILOG) $(IVFLAGS) -s tb_ntt -o $(OUT)/tb_ntt.vvp $(NTT_SRC)
	cd $(OUT) && $(VVP) -n tb_ntt.vvp | tee tb_ntt.log
	@grep -q "TB_NTT PASS" $(OUT)/tb_ntt.log

clean:
	rm -f $(OUT)/tb_uart.* $(OUT)/tb_uart_link.* $(OUT)/tb_ntt.*
