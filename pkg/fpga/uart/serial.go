package uart

import (
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"io"
	"time"

	"go.bug.st/serial"
)

// Command byte constants for the UART protocol.
const (
	cmdReadReg  = 0x01
	cmdWriteReg = 0x02
	cmdWriteData = 0x03
	cmdReadData  = 0x04
)

// Response status codes.
const (
	statusOK    = 0x00
	statusError = 0x01
)

// SerialDevice communicates with an FPGA PQC accelerator over a UART
// serial connection. This transport is slower than PCIe or AXI but
// works with any development board that has a USB-UART bridge.
type SerialDevice struct {
	port     serial.Port
	portName string
	timeout  time.Duration
}

// Open connects to an FPGA over UART at the specified port and baud rate.
// Common ports: /dev/ttyUSB0 (Linux), /dev/cu.usbserial-* (macOS).
func Open(portName string, baudRate int) (*SerialDevice, error) {
	mode := &serial.Mode{
		BaudRate: baudRate,
		DataBits: 8,
		StopBits: serial.OneStopBit,
		Parity:   serial.NoParity,
	}

	port, err := serial.Open(portName, mode)
	if err != nil {
		return nil, fmt.Errorf("opening serial port %s: %w", portName, err)
	}

	if err := port.SetReadTimeout(200 * time.Millisecond); err != nil {
		port.Close()
		return nil, fmt.Errorf("setting read timeout: %w", err)
	}

	return &SerialDevice{
		port:     port,
		portName: portName,
		timeout:  5 * time.Second,
	}, nil
}

// ReadReg reads a 32-bit CSR register at the given offset via the UART
// command protocol.
func (d *SerialDevice) ReadReg(offset uint32) (uint32, error) {
	resp, err := d.sendCommand(cmdReadReg, offset, nil)
	if err != nil {
		return 0, fmt.Errorf("UART read reg 0x%X: %w", offset, err)
	}
	if len(resp) != 4 {
		return 0, fmt.Errorf("UART read reg 0x%X: expected 4 bytes, got %d", offset, len(resp))
	}
	return binary.LittleEndian.Uint32(resp), nil
}

// WriteReg writes a 32-bit value to a CSR register at the given offset.
func (d *SerialDevice) WriteReg(offset, value uint32) error {
	buf := make([]byte, 4)
	binary.LittleEndian.PutUint32(buf, value)
	_, err := d.sendCommand(cmdWriteReg, offset, buf)
	if err != nil {
		return fmt.Errorf("UART write reg 0x%X: %w", offset, err)
	}
	return nil
}

// maxWriteChunk is the largest 0x03 payload the RTL bridge accepts
// (hdl/core/uart_axi_bridge.sv MAX_WRITE).
const maxWriteChunk = 256

// WriteData transfers data to the FPGA's data buffer via UART. The offset
// is a byte offset into the data buffer; large writes are split into
// frames of at most maxWriteChunk bytes.
func (d *SerialDevice) WriteData(offset uint32, data []byte) error {
	for start := 0; start < len(data); start += maxWriteChunk {
		end := min(start+maxWriteChunk, len(data))
		off := offset + uint32(start)
		if _, err := d.sendCommand(cmdWriteData, off, data[start:end]); err != nil {
			return fmt.Errorf("UART write data at 0x%X: %w", off, err)
		}
	}
	return nil
}

// ReadData reads data from the FPGA's data buffer via UART. The requested
// length is sent as the 4-byte payload of the read-data command.
func (d *SerialDevice) ReadData(offset uint32, length int) ([]byte, error) {
	// Encode the requested length in the address field and send read command.
	lenBuf := make([]byte, 4)
	binary.LittleEndian.PutUint32(lenBuf, uint32(length))
	resp, err := d.sendCommand(cmdReadData, offset, lenBuf)
	if err != nil {
		return nil, fmt.Errorf("UART read data at 0x%X: %w", offset, err)
	}
	if len(resp) != length {
		return nil, fmt.Errorf("UART read data at 0x%X: expected %d bytes, got %d", offset, length, len(resp))
	}
	return resp, nil
}

// Transport returns "uart".
func (d *SerialDevice) Transport() string { return "uart" }

// Close releases the serial port.
func (d *SerialDevice) Close() error {
	return d.port.Close()
}

// sendCommand sends a framed command and reads the response.
// Frame: [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC:4]
func (d *SerialDevice) sendCommand(cmd byte, addr uint32, data []byte) ([]byte, error) {
	dataLen := len(data)
	frame := make([]byte, 1+4+4+dataLen+4)

	frame[0] = cmd
	binary.LittleEndian.PutUint32(frame[1:5], addr)
	binary.LittleEndian.PutUint32(frame[5:9], uint32(dataLen))
	if dataLen > 0 {
		copy(frame[9:9+dataLen], data)
	}

	// CRC-32 over cmd+addr+len+data.
	crc := crc32.ChecksumIEEE(frame[:9+dataLen])
	binary.LittleEndian.PutUint32(frame[9+dataLen:], crc)

	// Drop stale bytes (e.g. from an earlier timed-out response) so the
	// next response header is parsed from the start of a frame.
	if err := d.port.ResetInputBuffer(); err != nil {
		return nil, fmt.Errorf("flushing input: %w", err)
	}
	if _, err := d.port.Write(frame); err != nil {
		return nil, fmt.Errorf("sending command: %w", err)
	}

	return d.readResponse()
}

// maxResponse bounds the LEN field of a response (data buffer size).
const maxResponse = 16384

// readFull fills buf from the port. The serial port returns (0, nil) when
// its per-read timeout expires, so a silent device is reported as an error
// after d.timeout instead of blocking forever.
func (d *SerialDevice) readFull(buf []byte) error {
	deadline := time.Now().Add(d.timeout)
	for got := 0; got < len(buf); {
		n, err := d.port.Read(buf[got:])
		if err != nil {
			return err
		}
		got += n
		if n == 0 && time.Now().After(deadline) {
			return fmt.Errorf("timeout after %s: got %d of %d bytes (%w)", d.timeout, got, len(buf), io.ErrUnexpectedEOF)
		}
	}
	return nil
}

// readResponse reads a framed response from the FPGA.
// Frame: [STATUS:1][LEN:4][DATA:LEN][CRC:4]
func (d *SerialDevice) readResponse() ([]byte, error) {
	// Read header: status + length.
	header := make([]byte, 5)
	if err := d.readFull(header); err != nil {
		return nil, fmt.Errorf("reading response header: %w", err)
	}

	status := header[0]
	respLen := binary.LittleEndian.Uint32(header[1:5])

	// Read data + CRC.
	if respLen > maxResponse {
		return nil, fmt.Errorf("response length %d exceeds %d (link out of sync?)", respLen, maxResponse)
	}
	tail := make([]byte, respLen+4)
	if err := d.readFull(tail); err != nil {
		return nil, fmt.Errorf("reading response body: %w", err)
	}

	// Verify CRC over status+len+data.
	fullResp := append(header, tail[:respLen]...)
	expectedCRC := binary.LittleEndian.Uint32(tail[respLen:])
	actualCRC := crc32.ChecksumIEEE(fullResp)
	if expectedCRC != actualCRC {
		return nil, fmt.Errorf("CRC mismatch: expected 0x%08X, got 0x%08X", expectedCRC, actualCRC)
	}

	if status != statusOK {
		return nil, fmt.Errorf("FPGA error status: 0x%02X", status)
	}

	return tail[:respLen], nil
}
