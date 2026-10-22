//
//    Copyright (C) 2024 The University of Tokyo
//
//    File:          /sakura-x-shell-ctrl/ft2232h_interface.v
//    Project:       sakura-x-shell
//    Author:        Takuya Kojima in The University of Tokyo (tkojima@hal.ipc.i.u-tokyo.ac.jp)
//    Created Date:  27-03-2024 20:57:52
//    Last Modified: 27-03-2024 20:57:52
//

`include "def.v"

module ft2232_fifo_interface #(
	parameter SYSCLOCK_PERIOD = 20
) (
	input clk,
	input reset_n,
	// to/from FT2232H
	input i_ftdi_rxf_n,
	input i_ftdi_txe_n,
	output o_ftdi_read_enable_n,
	output o_ftdi_write_enable_n,
	inout [`BYTE_B] io_ftdi_data,
	// from FIFO channel
	input [`BYTE_B] i_fifo_read_data,
	input i_fifo_empty,
	output o_fifo_read_enable,
	// to FIFO channel
	output [`BYTE_B] o_fifo_write_data,
	input i_fifo_full,
	output o_fifo_write_enable,
	// status
	output o_busy
);

	// function to calculate ceil(a/b)
	function integer ceil_div;
		input integer a, b;
		begin
			if (b == 0) begin
				ceil_div = 0; // error handling
			end else begin
				ceil_div = (a + b - 1) / b;
			end
		end
	endfunction
	// function to calculate clog2
	function integer clog2;
		input integer value;
		begin
			value = value - 1;
			for (clog2 = 0; value > 0; clog2 = clog2 + 1) begin
				value = value >> 1;
			end
		end
	endfunction

	// latch rxf_n and txe_n
	reg r_ftdi_rxf_n, r_ftdi_txe_n;
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_ftdi_rxf_n <= `FALSE_;
			r_ftdi_txe_n <= `FALSE_;
		end else begin
			r_ftdi_rxf_n <= i_ftdi_rxf_n;
			r_ftdi_txe_n <= i_ftdi_txe_n;
		end
	end

	// fifo write data
	reg [`BYTE_B] r_fifo_write_data, r_ftdi_write_data;

	// state machine
	localparam STATE_W = 8;
	localparam STATE_IDLE_B = 0;
	localparam STATE_IDLE = 1 << STATE_IDLE_B;			// 0x01
	localparam STATE_RD_SETUP_B = 1;
	localparam STATE_RD_SETUP = 1 << STATE_RD_SETUP_B;	// 0x02
	localparam STATE_RD_FETCH_B = 2;
	localparam STATE_RD_FETCH = 1 << STATE_RD_FETCH_B;	// 0x04
	localparam STATE_RD_PULSE_B = 3;
	localparam STATE_RD_PULSE = 1 << STATE_RD_PULSE_B;	// 0x08
	localparam STATE_RD_ENQUEUE_B = 4;
	localparam STATE_RD_ENQUEUE = 1 << STATE_RD_ENQUEUE_B;	// 0x10
	localparam STATE_WD_DEQUEUE_B = 5;
	localparam STATE_WD_DEQUEUE = 1 << STATE_WD_DEQUEUE_B;	// 0x20
	localparam STATE_WD_SETUP_B = 6;
	localparam STATE_WD_SETUP = 1 << STATE_WD_SETUP_B;	// 0x40
	localparam STATE_WD_PULSE_B = 7;
	localparam STATE_WD_PULSE = 1 << STATE_WD_PULSE_B;	// 0x80

	reg [STATE_W - 1:0] r_state;

	// timing parameters
	localparam PLUSE_CYCLES = ceil_div(30, SYSCLOCK_PERIOD);
	localparam RD_DATA_SETUP = ceil_div(14, SYSCLOCK_PERIOD);
	localparam RXF_SETUP = ceil_div(14, SYSCLOCK_PERIOD);

	localparam COUNTER_WIDTH = clog2(PLUSE_CYCLES);
	reg [COUNTER_WIDTH:0] r_counter;

	wire w_rxf_ready = (r_counter + 1 >= RXF_SETUP);

	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_state <= STATE_IDLE;
			r_counter <= 0;
		end else begin
			case (r_state)
				STATE_IDLE: begin
					if (r_ftdi_rxf_n == `TRUE_ && !i_fifo_full) begin
						// data arrived and fifo not full
						r_state <= STATE_RD_SETUP;
					end else if (r_ftdi_txe_n == `TRUE_ && !i_fifo_empty) begin
						// send data exists and ftdi not busy
						r_state <= STATE_WD_DEQUEUE;
					end
					r_counter <= 0;
				end
				STATE_RD_SETUP: begin
					// wait until data becomes stable
					if (r_counter >= RD_DATA_SETUP) begin
						r_state <= STATE_RD_FETCH;
					end
					r_counter <= r_counter + 1;
				end
				STATE_RD_FETCH: begin
					// latch data
					r_counter <= r_counter + 1;
					r_state <= STATE_RD_PULSE;
				end
				STATE_RD_PULSE: begin
					// keep RD for PULSE cycles
					if (r_counter >= PLUSE_CYCLES) begin
						r_state <= STATE_RD_ENQUEUE;
						r_counter <= 0;
					end else begin
						r_counter <= r_counter + 1;
					end
				end
				STATE_RD_ENQUEUE: begin
					// wait enough time for rxf to rise again
					if (w_rxf_ready) begin
						r_state <= STATE_IDLE;
					end
					r_counter <= r_counter + 1;
				end
				STATE_WD_DEQUEUE: begin
					// dequeue transferred data from fifo
					r_state <= STATE_WD_SETUP;
				end
				STATE_WD_SETUP: begin
					// wait until txe deasserted
					r_state <= STATE_WD_PULSE;
				end
				STATE_WD_PULSE: begin
					// keep WR for PULSE cycles
					if (r_counter >= PLUSE_CYCLES) begin
						r_state <= STATE_IDLE;
					end
					r_counter <= r_counter + 1;
				end
			endcase
		end
	end

	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_fifo_write_data <= 0;
		end else if(r_state[STATE_RD_FETCH_B]) begin
			r_fifo_write_data <= io_ftdi_data;
		end
	end

	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_ftdi_write_data <= 0;
		end else if(r_state[STATE_WD_DEQUEUE_B]) begin
			r_ftdi_write_data <= i_fifo_read_data;
		end
	end

	// output control signals
	assign o_ftdi_read_enable_n = (r_state[STATE_RD_SETUP_B] |
								  r_state[STATE_RD_FETCH_B] |
								  r_state[STATE_RD_PULSE_B]) ? `ENABLE_ : `DISABLE_;

	assign o_ftdi_write_enable_n = (r_state[STATE_WD_PULSE_B]) ? `ENABLE_ : `DISABLE_;
	assign o_fifo_read_enable = r_state[STATE_WD_DEQUEUE_B];
	assign o_fifo_write_enable = r_state[STATE_RD_ENQUEUE_B] & w_rxf_ready;

	// output data
	assign o_fifo_write_data = r_fifo_write_data;

	// tri-state to databus
	assign io_ftdi_data = (r_state[STATE_WD_PULSE_B]
							| r_state[STATE_WD_SETUP_B]) ? r_ftdi_write_data : 8'bz;

	assign o_busy = (r_state != STATE_IDLE);
endmodule