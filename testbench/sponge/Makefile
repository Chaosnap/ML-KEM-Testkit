# Icarus Verilog regression for keccak_sponge against Python hashlib.
#
#   make -C testbench/sponge

ROOT  := $(abspath ../..)
RTL   := $(ROOT)/hdl/core
OUT   := $(ROOT)/build/sponge
TB    := $(abspath .)

IVERILOG ?= iverilog
VVP      ?= vvp
IVFLAGS  := -g2012 -Wall -Wno-timescale -Wno-implicit-dimensions -Wno-sensitivity-entire-array -Wno-sensitivity-entire-vector

.PHONY: all clean

all:
	mkdir -p $(OUT)
	python3 $(TB)/gen_vectors.py $(OUT)/sponge_vectors.txt
	$(IVERILOG) $(IVFLAGS) -s tb_sponge -o $(OUT)/tb_sponge.vvp $(TB)/tb_sponge.sv $(RTL)/keccak_sponge.sv $(RTL)/keccak_round.sv
	cd $(OUT) && $(VVP) -n tb_sponge.vvp +vec=$(OUT)/sponge_vectors.txt | tee tb_sponge.log
	@grep -q "TB_SPONGE PASS" $(OUT)/tb_sponge.log

clean:
	rm -rf $(OUT)
