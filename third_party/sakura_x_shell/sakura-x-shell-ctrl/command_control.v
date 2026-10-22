//
//    Copyright (C) 2024 The University of Tokyo
//    
//    File:          /sakura-x-shell-ctrl/command_control.v
//    Project:       tkojima
//    Author:        Takuya Kojima in The University of Tokyo (tkojima@hal.ipc.i.u-tokyo.ac.jp)
//    Created Date:  25-03-2024 14:14:33
//    Last Modified: 25-01-2025 03:57:06
//


`include "def.v"

module command_control #(
	parameter RESET_CYCLES = 100
) (
	input clk,
	input reset_n,
	// from fifo
	input [`BYTE_B] i_fifo_data,
	input i_fifo_empty,
	output o_fifo_read_enable,
	input i_fifo_almost_empty,
	// to fifo
	output [`BYTE_B] o_fifo_data,
	input i_fifo_full,
	output o_fifo_write_enable,
	// to interconnect
	output o_addr_valid,
	output o_write_enable,
	output o_write_data_valid,
	input  i_addr_ready,
	input  i_write_data_ready,
	input  i_read_data_valid,
	output o_read_data_ready,
	output [`WORD_B] o_common, //shared for addr and write data
	input [`WORD_B] i_read_data,
	// soft reset
	output o_sw_reset_n,
	// status
	output o_busy
);

	// command decode
	reg [`CMD_W-1:0] r_command;
	reg [2:0] r_cmd_byte_count;
	wire [`PREPOST_AMBLE_W-1:0] w_preamble, w_postamble;
	wire [`CMD_TYPE_W-1:0] w_cmd_type;
	wire [`CMD_TYPE_ATTR_W-1:0] w_cmd_type_attr;
	wire [`CMD_ARGS_W-1:0] w_cmd_args;
	assign {w_preamble, w_cmd_type, w_cmd_type_attr, w_cmd_args, w_postamble} = r_command;

	wire w_error_cmd;
	assign w_error_cmd = (w_preamble != `PREAMBLE) || (w_postamble != `POSTAMBLE);
	wire w_read_cmd, w_write_cmd, w_reset_cmd;
	assign w_read_cmd = (w_cmd_type == `CMD_READ);
	assign w_write_cmd = (w_cmd_type == `CMD_WRITE);
	assign w_reset_cmd = (w_cmd_type == `CMD_RESET) && w_cmd_type_attr == 0;
	wire w_unknown_cmd;
	assign w_unknown_cmd = !w_read_cmd && !w_write_cmd && !w_reset_cmd;

	// ack data
	wire [`BYTE_B] w_ack_data;
	assign w_ack_data = (w_error_cmd) ? `RESPONSE_CMD_ERR :
						(w_unknown_cmd) ? `RESPONSE_UNKNOWN_CMD_ERR :
						`RESPONSE_OK;

	// read, write status
	reg [`WORD_B] r_addr;
	reg [`CMD_TYPE_ATTR_W-1:0] r_word_count, r_word_size;
	reg [`WORD_B] r_read_data, r_write_data;
	reg [2:0] r_byte_count;
	// big endian
	wire [`BYTE_B] w_read_byte = r_read_data[8 * r_byte_count +: 8];

	// reset control
	reg [6:0] r_reset_cycles;

	// state machine
	reg r_fifo_read_enable;
	localparam STATE_W = 12;
	localparam STATE_WAIT_B = 0;
	localparam STATE_WAIT = 1 << STATE_WAIT_B;							// 0x01
	localparam STATE_GET_COMMAND_B = 1;
	localparam STATE_GET_COMMAND = 1 << STATE_GET_COMMAND_B;			// 0x02
	localparam STATE_ACK_COMMAND_B = 2;
	localparam STATE_ACK_COMMAND = 1 << STATE_ACK_COMMAND_B;			// 0x04
	localparam STATE_SET_READ_ADDR_B = 3;
	localparam STATE_SET_READ_ADDR = 1 << STATE_SET_READ_ADDR_B;		// 0x08
	localparam STATE_READ_DATA_B = 5;
	localparam STATE_READ_DATA = 1 << STATE_READ_DATA_B;				// 0x20
	localparam STATE_READ_DATA_ACK_B = 6;
	localparam STATE_READ_DATA_ACK = 1 << STATE_READ_DATA_ACK_B;		// 0x40
	localparam STATE_READ_DATA_SEND_B = 7;
	localparam STATE_READ_DATA_SEND = 1 << STATE_READ_DATA_SEND_B;		// 0x80
	localparam STATE_SET_WRITE_ADDR_B = 8;
	localparam STATE_SET_WRITE_ADDR = 1 << STATE_SET_WRITE_ADDR_B;		// 0x100
	localparam STATE_FETCH_WRITE_DATA_B = 9;
	localparam STATE_FETCH_WRITE_DATA = 1 << STATE_FETCH_WRITE_DATA_B;	// 0x200
	localparam STATE_WRITE_DATA_B = 10;
	localparam STATE_WRITE_DATA = 1 << STATE_WRITE_DATA_B;				// 0x400
	localparam STATE_RESET_B = 11;
	localparam STATE_RESET = 1 << STATE_RESET_B;						// 0x800


	reg [STATE_W-1:0] r_state;

	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_state <= STATE_WAIT;
			r_word_count <= 0;
			r_word_size <= 0;
			r_reset_cycles <= 0;
		end else begin
			case (r_state)
				STATE_WAIT: begin
					if (!i_fifo_empty) begin
						r_state <= STATE_GET_COMMAND;
					end
				end
				STATE_GET_COMMAND: begin
					if (r_cmd_byte_count == `CMD_BYTE_LEN - 1) begin
						// command received
						r_state <= STATE_ACK_COMMAND;
					end else if (!i_fifo_almost_empty) begin
						// next byte
						r_state <= STATE_GET_COMMAND;
					end else begin
						// wait for next byte
						r_state <= STATE_WAIT;
					end
				end
				STATE_ACK_COMMAND: begin
					// wait until fifo is not full to send ack
					if (!i_fifo_full) begin
						if (w_error_cmd || w_unknown_cmd) begin
							// error
							r_state <= STATE_WAIT;
						end else begin
							if (w_read_cmd) begin
								// read command
								r_state <= STATE_SET_READ_ADDR;
								r_word_size <= w_cmd_type_attr;
								r_word_count <= 0;
							end else if (w_write_cmd) begin
								// write command
								r_state <= STATE_SET_WRITE_ADDR;
								r_word_size <= w_cmd_type_attr;
								r_word_count <= 0;
							end else if (w_reset_cmd) begin
								// reset command
								r_state <= STATE_RESET;
								r_reset_cycles <= RESET_CYCLES;
							end
						end
					end
				end
				STATE_SET_READ_ADDR: begin
					if (i_addr_ready) begin
						r_state <= STATE_READ_DATA;
					end
				end
				STATE_READ_DATA: begin
					if (i_read_data_valid) begin
						r_state <= STATE_READ_DATA_ACK;
					end
				end
				STATE_READ_DATA_ACK: begin
					r_state <= STATE_READ_DATA_SEND;
				end
				STATE_READ_DATA_SEND: begin
					if (!i_fifo_full) begin
						if (r_byte_count == 0) begin // last byte enqueued
							if (r_word_count == r_word_size) begin
								r_state <= STATE_WAIT;
							end else begin
								r_word_count <= r_word_count + 1;
								r_state <= STATE_SET_READ_ADDR;
							end
						end
					end
				end
				STATE_SET_WRITE_ADDR: begin
					if (i_addr_ready) begin
						r_state <= STATE_FETCH_WRITE_DATA;
					end
				end
				STATE_FETCH_WRITE_DATA: begin
					if (!i_fifo_empty && r_byte_count == 0) begin
						r_state <= STATE_WRITE_DATA;
					end
				end
				STATE_WRITE_DATA: begin
					if (i_write_data_ready) begin
						if (r_word_count == r_word_size) begin
							r_state <= STATE_WAIT;
						end else begin
							r_state <= STATE_SET_WRITE_ADDR;
							r_word_count <= r_word_count + 1;
						end
					end
				end
				STATE_RESET: begin
					if (r_reset_cycles == 0) begin
						r_state <= STATE_WAIT;
					end else begin
						r_reset_cycles <= r_reset_cycles - 1;
					end
				end
			endcase
		end
	end

	// command buffer
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_command <= 0;
			r_cmd_byte_count <= 0;
		end else if (r_state[STATE_GET_COMMAND_B]) begin
			r_command <= {r_command[`CMD_W-`BYTE_W-1:0], i_fifo_data};
			if (r_cmd_byte_count == `CMD_BYTE_LEN - 1) begin
				r_cmd_byte_count <= 0;
			end else begin
				r_cmd_byte_count <= r_cmd_byte_count + 1;
			end
		end
	end

	// read data buffer
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_read_data <= 0;
		end else if (i_read_data_valid) begin
			r_read_data <= i_read_data;
		end
	end

	// write data buffer
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_write_data <= 0;
		end else if (r_state[STATE_FETCH_WRITE_DATA_B] && !i_fifo_empty) begin
			r_write_data <= {r_write_data[`WORD_W-`BYTE_W:0], i_fifo_data};
		end
	end

	// byte counter
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_byte_count <= 0;
		end else begin
			case (r_state)
				STATE_READ_DATA_SEND: begin
					if (!i_fifo_full) begin
						r_byte_count <= r_byte_count - 1;
					end
				end
				STATE_READ_DATA_ACK: begin
					r_byte_count <= 3; // big endian
				end
				STATE_SET_WRITE_ADDR: begin
					r_byte_count <= 3;
				end
				STATE_FETCH_WRITE_DATA: begin
					if (!i_fifo_empty) begin
						r_byte_count <= r_byte_count - 1;
					end
				end
			endcase
		end
	end


	// address buffer
	always @(posedge clk or negedge reset_n) begin
		if (!reset_n) begin
			r_addr <= 0;
		end else if (r_state[STATE_ACK_COMMAND_B]) begin
			if (w_read_cmd || w_write_cmd) begin
				// latch address when read or write command is received
				r_addr <= w_cmd_args;
			end
		end
	end

	// output signals
	assign o_fifo_read_enable = r_state[STATE_GET_COMMAND_B] || r_state[STATE_FETCH_WRITE_DATA_B] && !i_fifo_empty;
	assign o_fifo_write_enable = (r_state[STATE_ACK_COMMAND_B] ||
								  r_state[STATE_READ_DATA_SEND_B]) && !i_fifo_full;
	assign o_fifo_data = (r_state[STATE_ACK_COMMAND_B]) ? w_ack_data :
						 (r_state[STATE_READ_DATA_SEND_B]) ? w_read_byte : 0;

	assign o_addr_valid = r_state[STATE_SET_READ_ADDR_B] | r_state[STATE_SET_WRITE_ADDR_B];
	assign o_write_enable = r_state[STATE_SET_WRITE_ADDR_B] | r_state[STATE_FETCH_WRITE_DATA_B] | r_state[STATE_WRITE_DATA_B];
	assign o_write_data_valid = r_state[STATE_WRITE_DATA_B];
	assign o_read_data_ready = r_state[STATE_READ_DATA_ACK_B];
	assign o_common = (!r_state[STATE_WRITE_DATA_B]) ? r_addr + r_word_count * 4 :
						r_write_data;
	assign o_busy = r_state != STATE_WAIT;

	assign o_sw_reset_n = r_state[STATE_RESET_B] ? `ENABLE_ : `DISABLE_;

endmodule

