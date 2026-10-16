/*
 * arty_z7_uart_fwd - bare-metal byte forwarder for the Arty Z7-20 PS variant.
 *
 * The Arty Z7's on-board USB serial reaches only PS UART0 (MIO 14/15); the
 * ML-KEM core's UART bridge lives in the PL and is wired to PS UART1 over
 * EMIO (hdl/xilinx/arty_z7_ps/arty_z7_ps_top.sv). This program copies every
 * byte from one UART to the other, both directions, so the host sees the
 * PL bridge exactly as on the Arty A7: same protocol, same 115200 8N1.
 *
 * Runs on CPU0 from on-chip memory (link.ld), no interrupts, no C library,
 * no DDR. ps7_init (scripts/board/boot_arty_z7_ps.py) has already set up clocks,
 * MIO and the PS<->PL level shifters; the UARTs are (re)programmed here so
 * the program does not depend on ps7_init's peripheral section.
 *
 * Side-channel note: CPU0 busy-polls the two UARTs the whole time, which is
 * extra switching activity on the same die as the core being measured. For
 * the lowest-noise captures use the pure-PL variant (hdl/xilinx/arty_z7,
 * external USB-UART on Pmod JA) instead.
 *
 * Register map: Zynq-7000 TRM (UG585), Appendix B, "UART Controller".
 */

#include <stdint.h>

#define UART0_BASE 0xE0000000u  /* MIO 14/15 -> on-board USB serial (host). */
#define UART1_BASE 0xE0001000u  /* EMIO      -> PL uart_rx / uart_tx.       */

#define REG(base, off) (*(volatile uint32_t *)((base) + (off)))
#define UART_CR      0x00u  /* Control.                 */
#define UART_MR      0x04u  /* Mode.                    */
#define UART_IDR     0x0Cu  /* Interrupt disable.       */
#define UART_BAUDGEN 0x18u  /* Baud rate generator: CD. */
#define UART_SR      0x2Cu  /* Channel status.          */
#define UART_FIFO    0x30u  /* TX / RX FIFO.            */
#define UART_BAUDDIV 0x34u  /* Baud rate divider: BDIV. */

#define CR_RXRES   (1u << 0)
#define CR_TXRES   (1u << 1)
#define CR_RXEN    (1u << 2)
#define CR_RXDIS   (1u << 3)
#define CR_TXEN    (1u << 4)
#define CR_TXDIS   (1u << 5)
#define MR_8N1     0x20u      /* CHRL = 8 bit, PAR = none, NBSTOP = 1, CLKS = uart_ref_clk. */
#define SR_RXEMPTY (1u << 1)
#define SR_TXFULL  (1u << 4)

/* 115200 baud from the 100 MHz uart_ref_clk (PCW_UART_PERIPHERAL_FREQMHZ,
 * checked by scripts/vivado/build_arty_z7_ps.tcl):
 * 100e6 / (CD * (BDIV + 1)) = 100e6 / (124 * 7) = 115207 baud (+0.006 %),
 * the same divisors ps7_init programs. */
#define BAUDGEN_115200 124u
#define BAUDDIV_115200 6u

static void uart_init(uint32_t base)
{
    REG(base, UART_IDR)     = 0x1FFFu;            /* Polling only. */
    REG(base, UART_CR)      = CR_TXDIS | CR_RXDIS;
    REG(base, UART_BAUDGEN) = BAUDGEN_115200;
    REG(base, UART_BAUDDIV) = BAUDDIV_115200;
    REG(base, UART_MR)      = MR_8N1;
    REG(base, UART_CR)      = CR_TXRES | CR_RXRES | CR_TXEN | CR_RXEN;
}

/* Move one byte from src to dst if one is waiting and dst has room. Both
 * sides run at the same baud rate and this loop is far faster than either,
 * so the 64-byte FIFOs never back up. */
static inline void pump(uint32_t src, uint32_t dst)
{
    if (!(REG(src, UART_SR) & SR_RXEMPTY) && !(REG(dst, UART_SR) & SR_TXFULL))
        REG(dst, UART_FIFO) = REG(src, UART_FIFO);
}

int main(void)
{
    uart_init(UART0_BASE);
    uart_init(UART1_BASE);
    for (;;) {
        pump(UART0_BASE, UART1_BASE);   /* host -> PL */
        pump(UART1_BASE, UART0_BASE);   /* PL   -> host */
    }
}
