# Build the Arty Z7 UART forwarder (host side; needs arm-none-eabi-gcc,
# e.g. `brew install arm-none-eabi-gcc`).
#
#   make -C sw/arty_z7_uart_fwd
#
# Output: build/arty_z7_uart_fwd/uart_fwd.elf, loaded by
# scripts/board/boot_arty_z7_ps.py (or scripts/vivado/load_arty_z7_ps.tcl).

ROOT   := $(abspath ../..)
OUT    := $(ROOT)/build/arty_z7_uart_fwd
CROSS  ?= arm-none-eabi-
CFLAGS := -mcpu=cortex-a9 -marm -O2 -ffreestanding -nostdlib -Wall -Wextra -Werror
LDFLAGS := -T link.ld -Wl,--build-id=none

.PHONY: all clean

all: $(OUT)/uart_fwd.elf

$(OUT)/uart_fwd.elf: start.S main.c link.ld
	mkdir -p $(OUT)
	$(CROSS)gcc $(CFLAGS) $(LDFLAGS) -o $@ start.S main.c
	$(CROSS)objdump -d $@ > $(OUT)/uart_fwd.lst
	$(CROSS)size $@

clean:
	rm -rf $(OUT)
