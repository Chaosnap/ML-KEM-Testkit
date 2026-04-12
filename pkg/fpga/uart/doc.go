// Package uart provides UART serial communication with FPGA PQC accelerators
// on development boards.
//
// UART is the simplest transport and useful for early development and
// debugging on boards like the Arty A7 that may not have PCIe. It uses a
// simple command/response protocol over a serial connection.
//
// # Protocol
//
// Commands are sent as fixed-size frames:
//
//	[CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC:2]
//
// CMD values:
//   - 0x01: Read register (ADDR=reg offset, LEN=0)
//   - 0x02: Write register (ADDR=reg offset, LEN=4, DATA=value)
//   - 0x03: Write data (ADDR=buffer offset, LEN=data length, DATA=payload)
//   - 0x04: Read data (ADDR=buffer offset, LEN=requested length)
//
// Response frame:
//
//	[STATUS:1][LEN:4][DATA:LEN][CRC:2]
//
// # Usage
//
//	dev, err := uart.Open("/dev/ttyUSB0", 115200)
//	defer dev.Close()
//	val, _ := dev.ReadReg(fpga.RegALGID)
package uart
