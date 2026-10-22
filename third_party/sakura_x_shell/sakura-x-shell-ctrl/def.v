//
//    Copyright (C) 2024 The University of Tokyo
//
//    File:          /sakura-x-shell-ctrl/def.v
//    Project:       sakura-x-shell
//    Author:        Takuya Kojima in The University of Tokyo (tkojima@hal.ipc.i.u-tokyo.ac.jp)
//    Created Date:  27-03-2024 20:57:37
//    Last Modified: 27-03-2024 20:57:37
//


// active high signals
`define ENABLE 1'b1
`define DISABLE 1'b0
`define TRUE 1'b1
`define FALSE 1'b0

// active low signals
`define ENABLE_ 1'b0
`define DISABLE_ 1'b1
`define TRUE_ 1'b0
`define FALSE_ 1'b1

// data width
`define BYTE_W 8
`define BYTE_B (`BYTE_W - 1):0
`define WORD_W 32
`define WORD_B (`WORD_W - 1):0

// command format
`define PREAMBLE 4'h8
`define POSTAMBLE 4'h1
`define PREPOST_AMBLE_W 4

// 6 bytes command
// | preamble 1/2 bytes | cmd 1 byte | args 4 bytes | postamble 1/2 bytes |
`define CMD_W 48
`define CMD_BYTE_LEN 6
`define CMD_TYPE_W 4
`define CMD_TYPE_ATTR_W 4
`define CMD_ARGS_W 32
// reset command: 0x00
`define CMD_RESET 8'h00
// read command: 0x1_data_len, args = read address
`define CMD_READ 4'h1
// write command: 0x2_data_len, args = write address
`define CMD_WRITE 4'h2

// 1 byte response
`define RESPONSE_OK 8'h00
`define RESPONSE_CMD_ERR 8'h01
`define RESPONSE_UNKNOWN_CMD_ERR 8'h02


