@ Reset entry for arty_z7_uart_fwd: CPU0, ARM mode, caches/MMU off (as left
@ by `rst -processor`). Polling only, so interrupts stay masked.

    .syntax unified
    .arm
    .section .text.start, "ax"
    .global _start
_start:
    cpsid   aif
    ldr     sp, =__stack_top
    bl      main
1:  wfe
    b       1b
