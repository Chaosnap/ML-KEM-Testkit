//
//    Copyright (C) 2026 University of Tsukuba
//
//    File:          /hardware/sakura-x-shell/sakura-x-shell-ctrl/ctrl_top.v
//    Project:       chipwhisperer-enhanced-plugins
//    Author:        Takuya Kojima in University of Tsukuba (tkojima@lila.cs.tsukuba.ac.jp)
//    Created Date:  27-03-2024 20:57:27
//    Last Modified: 27-09-2026 16:49:27
//


`timescale 1ns / 1ps
`include "def.v"

module ctrl_top #(
	// timing params (default: 50MHz is assumed)
	parameter SYSCLOCK_PERIOD = 20 // needed for timing control for FT2232H
) (
	// reset
	input reset_n,

	// clock source d
	input i_clk_osc,
	output o_clk_osc_inh_n,

	// on-board LEDs
	output [9:0] o_led,

	// FTDI FIFO channel (Channel A)
	input i_ftdi_rxf_n,
	input i_ftdi_txe_n,
	output o_ftdi_rd_n,
	output o_ftdi_wr_n,
	output o_ftdi_siwua,
	inout [7:0] io_ftdi_adbus,
	// FTDI UART Channel (Channel B)
	// Only RTS is used for reset, so other signals are not connected.
	input i_ftdi_rts_n,

	// interconnect
	output o_bus_clk,
	output o_bus_reset_n,
	output o_addr_valid,
	output o_write_enable,
	output o_write_data_valid,
	input  i_addr_ready,
	input  i_write_data_ready,
	input  i_read_data_valid,
	output o_read_data_ready,
	output [`WORD_B] o_common, //shared for addr and write data
	input [`WORD_B] i_read_data
);
	wire clk;
	wire w_sw_reset_n;
	wire w_reset_request_n = reset_n & i_ftdi_rts_n;
	(* ASYNC_REG = "TRUE" *) reg [1:0] r_reset_sync;
	wire w_hw_reset_n = r_reset_sync[1];

	// Assert immediately; release reset synchronously for all clk-domain logic.
	always @(posedge clk or negedge w_reset_request_n) begin
		if (!w_reset_request_n) begin
			r_reset_sync <= 2'b00;
		end else begin
			r_reset_sync <= {r_reset_sync[0], 1'b1};
		end
	end

	wire w_blinker_led;
	blinker #(
		.MSB(26) // blink per 1.34 sec when 50MHz
	) blinker0 (
		.clk(clk),
		.reset_n(w_hw_reset_n),
		.led(w_blinker_led)
	);

	// Buffer -> FTDI
	wire w_buf2ftdi_empty;
	wire [`BYTE_B] w_buf2ftdi_data;
	wire w_ftdi2buf_read_enable;
	wire [`BYTE_B] w_bridge2buf_data;
	wire w_buf2bridge_full;
	wire w_bridge2buf_write_enable;

	fifo buffer_to_ftdi (
		.clk(clk),
		.reset_n(w_hw_reset_n),
		.i_data(w_bridge2buf_data),
		.i_write_enable(w_bridge2buf_write_enable),
		.o_full(w_buf2bridge_full),
		.o_data(w_buf2ftdi_data),
		.i_read_enable(w_ftdi2buf_read_enable),
		.o_empty(w_buf2ftdi_empty),
		.o_almost_empty()
	);

	// FTDI -> Buffer
	wire w_buf2ftdi_full;
	wire [`BYTE_B] w_ftdi2buf_data;
	wire w_ftdi2buf_write_enable;
	wire [`BYTE_B] w_buf2bridge_data;
	wire w_buf2bridge_empty;
	wire w_bridge2buf_read_enable;
	wire w_buf2bridge_almost_empty;
	fifo buffer_from_ftdi (
		.clk(clk),
		.reset_n(w_hw_reset_n),
		.i_data(w_ftdi2buf_data),
		.i_write_enable(w_ftdi2buf_write_enable),
		.o_full(w_buf2ftdi_full),
		.o_data(w_buf2bridge_data),
		.i_read_enable(w_bridge2buf_read_enable),
		.o_empty(w_buf2bridge_empty),
		.o_almost_empty(w_buf2bridge_almost_empty)
	);

	// interface to FT2232H
	wire w_ftdi_interface_busy;
	ft2232_fifo_interface #(
		.SYSCLOCK_PERIOD(SYSCLOCK_PERIOD)
	) ft2232_interface0 (
		.clk(clk),
		.reset_n(w_hw_reset_n),
		// to/from FT2232H module
		.i_ftdi_rxf_n(i_ftdi_rxf_n),
		.i_ftdi_txe_n(i_ftdi_txe_n),
		.o_ftdi_read_enable_n(o_ftdi_rd_n),
		.o_ftdi_write_enable_n(o_ftdi_wr_n),
		.io_ftdi_data(io_ftdi_adbus),
		// to/from FIFOs
		.i_fifo_read_data(w_buf2ftdi_data),
		.i_fifo_empty(w_buf2ftdi_empty),
		.o_fifo_read_enable(w_ftdi2buf_read_enable),
		.o_fifo_write_data(w_ftdi2buf_data),
		.i_fifo_full(w_buf2ftdi_full),
		.o_fifo_write_enable(w_ftdi2buf_write_enable),
		// status
		.o_busy(w_ftdi_interface_busy)
	);

	// bridge between interconnect and FIFO while decoding commands
	wire w_comand_control_busy;
	command_control command_control0 (
		.clk(clk),
		.reset_n(w_hw_reset_n),
		// from FIFO
		.i_fifo_data(w_buf2bridge_data),
		.i_fifo_empty(w_buf2bridge_empty),
		.i_fifo_almost_empty(w_buf2bridge_almost_empty),
		.o_fifo_read_enable(w_bridge2buf_read_enable),
		// to FIFO
		.o_fifo_data(w_bridge2buf_data),
		.i_fifo_full(w_buf2bridge_full),
		.o_fifo_write_enable(w_bridge2buf_write_enable),
		// interconnect
		.o_addr_valid(o_addr_valid),
		.o_write_enable(o_write_enable),
		.o_write_data_valid(o_write_data_valid),
		.i_addr_ready(i_addr_ready),
		.i_write_data_ready(i_write_data_ready),
		.i_read_data_valid(i_read_data_valid),
		.o_read_data_ready(o_read_data_ready),
		.o_common(o_common),
		.i_read_data(i_read_data),
		// soft reset
		.o_sw_reset_n(w_sw_reset_n),
		// status
		.o_busy(w_comand_control_busy)
	);

	// sys clock generation using PLL IP
`ifdef SIM
	reg r_clk;
	initial begin
		r_clk <= 0;
	end
	always begin
		#(SYSCLOCK_PERIOD/2);
		r_clk <= ~r_clk;
	end
	assign clk = r_clk;
`else
	pll pll0(
		.CLK_IN1(i_clk_osc),
		.CLK_OUT1(clk),
		// Keep the clock running during an FTDI-requested reset.
		.RESET(!reset_n)
	);
`endif

	// disable inhibit function, i.e., always oscillate
	assign o_clk_osc_inh_n = `DISABLE_;

	assign o_ftdi_siwua = `DISABLE;


	// LED mapping
	// | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
	// | buf(in) full | buf(in) empty | buf(out) full | buf(out) empty | interface busy | command busy | blinker | N/A | N/A | N/A |
	assign o_led = {3'b0, w_blinker_led, w_comand_control_busy, w_ftdi_interface_busy, w_buf2bridge_empty, w_buf2ftdi_full, w_buf2ftdi_empty, w_buf2bridge_full};

	assign o_bus_reset_n = w_hw_reset_n & w_sw_reset_n;

	// interconnect clock
`ifdef SIM
	assign o_bus_clk = clk;
`else
	ODDR2 u0 (.D0(1'b0), .D1(1'b1), .C0(clk), .C1(~clk),
				.Q(o_bus_clk),    .CE(1'b1), .R(1'b0), .S(1'b0));
`endif

endmodule

module blinker #(
	parameter MSB = 26
) (
	input clk,
	input reset_n,
	output led
);
	reg [MSB:0] r_counter;
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_counter <= 0;
		end else begin
			r_counter <= r_counter + 1;
		end
	end

	assign led = r_counter[MSB];

endmodule
