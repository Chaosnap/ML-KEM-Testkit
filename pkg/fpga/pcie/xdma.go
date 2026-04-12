package pcie

import (
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
)

// XDMADevice communicates with an FPGA PQC accelerator via the Xilinx
// XDMA PCIe driver. It accesses CSR registers through the AXI-Lite BAR
// and performs bulk data transfer via DMA channels.
type XDMADevice struct {
	userFile *os.File // AXI-Lite BAR (/dev/xdma*_user)
	h2cFile  *os.File // Host-to-Card DMA (/dev/xdma*_h2c_0)
	c2hFile  *os.File // Card-to-Host DMA (/dev/xdma*_c2h_0)
	basePath string   // e.g., "/dev/xdma0"
}

// OpenXDMA opens an XDMA device by its base path (e.g., "/dev/xdma0").
// It opens the user (CSR), h2c (write DMA), and c2h (read DMA) channels.
// The caller must call Close when finished.
func OpenXDMA(basePath string) (*XDMADevice, error) {
	userPath := basePath + "_user"
	h2cPath := basePath + "_h2c_0"
	c2hPath := basePath + "_c2h_0"

	// Verify all device nodes exist before opening.
	for _, p := range []string{userPath, h2cPath, c2hPath} {
		if _, err := os.Stat(p); err != nil {
			return nil, fmt.Errorf("XDMA device node %s not found: %w (is the XDMA driver loaded?)", filepath.Base(p), err)
		}
	}

	userFile, err := os.OpenFile(userPath, os.O_RDWR, 0)
	if err != nil {
		return nil, fmt.Errorf("opening XDMA user channel: %w", err)
	}

	h2cFile, err := os.OpenFile(h2cPath, os.O_WRONLY, 0)
	if err != nil {
		userFile.Close()
		return nil, fmt.Errorf("opening XDMA h2c channel: %w", err)
	}

	c2hFile, err := os.OpenFile(c2hPath, os.O_RDONLY, 0)
	if err != nil {
		userFile.Close()
		h2cFile.Close()
		return nil, fmt.Errorf("opening XDMA c2h channel: %w", err)
	}

	return &XDMADevice{
		userFile: userFile,
		h2cFile:  h2cFile,
		c2hFile:  c2hFile,
		basePath: basePath,
	}, nil
}

// ReadReg reads a 32-bit CSR register at the given byte offset via the
// AXI-Lite BAR.
func (d *XDMADevice) ReadReg(offset uint32) (uint32, error) {
	buf := make([]byte, 4)
	n, err := d.userFile.ReadAt(buf, int64(offset))
	if err != nil {
		return 0, fmt.Errorf("XDMA read reg 0x%X: %w", offset, err)
	}
	if n != 4 {
		return 0, fmt.Errorf("XDMA read reg 0x%X: short read (%d bytes)", offset, n)
	}
	return binary.LittleEndian.Uint32(buf), nil
}

// WriteReg writes a 32-bit value to a CSR register at the given byte offset.
func (d *XDMADevice) WriteReg(offset, value uint32) error {
	buf := make([]byte, 4)
	binary.LittleEndian.PutUint32(buf, value)
	n, err := d.userFile.WriteAt(buf, int64(offset))
	if err != nil {
		return fmt.Errorf("XDMA write reg 0x%X: %w", offset, err)
	}
	if n != 4 {
		return fmt.Errorf("XDMA write reg 0x%X: short write (%d bytes)", offset, n)
	}
	return nil
}

// WriteData transfers data to the FPGA via the Host-to-Card DMA channel.
// The offset is the destination address in the FPGA's address space.
func (d *XDMADevice) WriteData(offset uint32, data []byte) error {
	n, err := d.h2cFile.WriteAt(data, int64(offset))
	if err != nil {
		return fmt.Errorf("XDMA DMA write at 0x%X: %w", offset, err)
	}
	if n != len(data) {
		return fmt.Errorf("XDMA DMA write at 0x%X: short write (%d/%d bytes)", offset, n, len(data))
	}
	return nil
}

// ReadData reads data from the FPGA via the Card-to-Host DMA channel.
// The offset is the source address in the FPGA's address space.
func (d *XDMADevice) ReadData(offset uint32, length int) ([]byte, error) {
	buf := make([]byte, length)
	n, err := d.c2hFile.ReadAt(buf, int64(offset))
	if err != nil {
		return nil, fmt.Errorf("XDMA DMA read at 0x%X: %w", offset, err)
	}
	if n != length {
		return nil, fmt.Errorf("XDMA DMA read at 0x%X: short read (%d/%d bytes)", offset, n, length)
	}
	return buf, nil
}

// Transport returns "pcie-xdma".
func (d *XDMADevice) Transport() string { return "pcie-xdma" }

// Close releases all file handles to the XDMA device.
func (d *XDMADevice) Close() error {
	var firstErr error
	if err := d.c2hFile.Close(); err != nil && firstErr == nil {
		firstErr = err
	}
	if err := d.h2cFile.Close(); err != nil && firstErr == nil {
		firstErr = err
	}
	if err := d.userFile.Close(); err != nil && firstErr == nil {
		firstErr = err
	}
	return firstErr
}
