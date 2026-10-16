/* On-chip memory only: after the BootROM, OCM's first 192 KB sit at
 * 0x0000_0000 (UG585 "OCM Address Map"); DDR is never used. */
ENTRY(_start)

MEMORY
{
    OCM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x30000
}

SECTIONS
{
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) } > OCM
    /* The program has no initialised or zero-initialised data; fail the
     * link if that ever changes, since nothing here copies or clears it. */
    .data : { *(.data*) } > OCM
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > OCM
    ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "uart_fwd: .data/.bss not supported")

    __stack_top = ORIGIN(OCM) + LENGTH(OCM);
}
