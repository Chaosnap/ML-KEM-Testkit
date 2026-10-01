`timescale 1ns / 1ps
// tb_uart.sv - Minimal UART testbench: one byte, uart_tx -> uart_rx
//
// Board parameters (100 MHz, 115200 8N1). The TB hands 0xA5 to uart_tx,
// the serial line is looped into uart_rx, and the received byte is checked.
// One frame is 10 bits x 8.68 us, so the whole run is about 90 us.
//
// Run:   make -C testbench/iverilog uart
// Wave:  build/sim/tb_uart.vcd   (see docs/IVERILOG_SIM_GUIDE.md)

module tb_uart;

    parameter int CLK_FREQ  = 100_000_000;
    parameter int BAUD_RATE = 115200;
    parameter logic [7:0] TX_BYTE = 8'hA5;   // 1010_0101: LSB first -> 1,0,1,0,0,1,0,1

    localparam int CPB = CLK_FREQ / BAUD_RATE;  // 868 clocks per bit.

    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk = ~clk;  // 100 MHz.

    logic [7:0] tx_data  = 8'h00;
    logic       tx_valid = 1'b0;
    logic       tx_ready;
    logic       line;               // Serial line: uart_tx.tx -> uart_rx.rx.
    logic [7:0] rx_data;
    logic       rx_valid;

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_tx (
        .clk(clk), .rst_n(rst_n), .tx(line),
        .data(tx_data), .valid(tx_valid), .ready(tx_ready)
    );

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_rx (
        .clk(clk), .rst_n(rst_n), .rx(line),
        .data(rx_data), .valid(rx_valid)
    );

    // One-cycle pulse at each point where uart_rx samples a data bit
    // (mid-bit), so the sampling instants are visible in the waveform.
    logic rx_sample;
    assign rx_sample = (u_rx.state == 3'd2) && (u_rx.clk_cnt == CPB - 1);

    initial begin
        $dumpfile("tb_uart.vcd");
        $dumpvars(0, tb_uart);

        $display("=== tb_uart: %0d Hz, %0d baud (%0d clk/bit), byte 0x%02h ===",
                 CLK_FREQ, BAUD_RATE, CPB, TX_BYTE);

        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (10) @(posedge clk);

        // Hand one byte to the transmitter (1-cycle valid pulse).
        @(negedge clk);
        tx_data  = TX_BYTE;
        tx_valid = 1'b1;
        $display("  %0d ns: uart_tx accepts 0x%02h", $time, TX_BYTE);
        @(negedge clk);
        tx_valid = 1'b0;

        @(posedge rx_valid);
        $display("  %0d ns: uart_rx valid, data = 0x%02h", $time, rx_data);

        // Let the stop bit finish so tx_ready returns high in the waveform.
        wait (tx_ready);
        repeat (CPB / 4) @(posedge clk);

        if (rx_data === TX_BYTE) $display("TB_UART PASS");
        else                     $display("TB_UART FAIL: got 0x%02h, expected 0x%02h", rx_data, TX_BYTE);
        $finish;
    end

    initial begin
        #200_000;  // 200 us watchdog.
        $display("TB_UART FAIL: timeout");
        $finish;
    end

endmodule
