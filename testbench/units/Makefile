# Icarus Verilog unit tests for the ML-KEM arithmetic units.
#
#   make -C testbench/units            # all three
#   make -C testbench/units modmul     # exhaustive a*b mod q (q^2 pairs, ~1 min)
#   make -C testbench/units pack       # Compress_d + ByteEncode_d, all x < q
#   make -C testbench/units unpack     # ByteDecode_d + Decompress_d, all fields

ROOT  := $(abspath ../..)
RTL   := $(ROOT)/hdl/core
OUT   := $(ROOT)/build/units
TB    := $(abspath .)

IVERILOG ?= iverilog
VVP      ?= vvp
IVFLAGS  := -g2012 -Wall -Wno-timescale -Wno-implicit-dimensions -Wno-sensitivity-entire-array -Wno-sensitivity-entire-vector

.PHONY: all modmul pack unpack vectors clean

all: modmul pack unpack

$(OUT):
	mkdir -p $(OUT)

vectors: | $(OUT)
	python3 $(TB)/gen_vectors.py $(OUT)

modmul: | $(OUT)
	$(IVERILOG) $(IVFLAGS) -s tb_modmul -o $(OUT)/tb_modmul.vvp $(TB)/tb_modmul.sv $(RTL)/mlkem_modmul.sv
	cd $(OUT) && $(VVP) -n tb_modmul.vvp | tee tb_modmul.log
	@grep -q "TB_MODMUL PASS" $(OUT)/tb_modmul.log

pack: vectors
	$(IVERILOG) $(IVFLAGS) -s tb_pack -o $(OUT)/tb_pack.vvp $(TB)/tb_pack.sv $(RTL)/mlkem_pack.sv
	cd $(OUT) && $(VVP) -n tb_pack.vvp +dir=$(OUT) | tee tb_pack.log
	@grep -q "TB_PACK PASS" $(OUT)/tb_pack.log

unpack: vectors
	$(IVERILOG) $(IVFLAGS) -s tb_unpack -o $(OUT)/tb_unpack.vvp $(TB)/tb_unpack.sv $(RTL)/mlkem_unpack.sv
	cd $(OUT) && $(VVP) -n tb_unpack.vvp +dir=$(OUT) | tee tb_unpack.log
	@grep -q "TB_UNPACK PASS" $(OUT)/tb_unpack.log

clean:
	rm -rf $(OUT)
