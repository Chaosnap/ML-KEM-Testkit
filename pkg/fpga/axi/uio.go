package axi

import (
	"encoding/binary"
	"fmt"
	"os"
	"syscall"
)

// UIODevice communicates with an FPGA PQC accelerator via a UIO (Userspace I/O)
// driver on Zynq SoC platforms. The PQC core's AXI-Lite register block and
// data buffer are memory-mapped into userspace.
type UIODevice struct {
	file     *os.File
	mmap     []byte
	baseAddr uintptr
	mapSize  int
}

// OpenUIO opens a UIO device and maps the specified region size into
// userspace. The devPath is typically "/dev/uio0" and mapSize should
// cover both CSR registers and the data buffer region.
func OpenUIO(devPath string, mapSize int) (*UIODevice, error) {
	f, err := os.OpenFile(devPath, os.O_RDWR|os.O_SYNC, 0)
	if err != nil {
		return nil, fmt.Errorf("opening UIO device %s: %w", devPath, err)
	}

	// Map the entire region (CSR + data buffer) from offset 0.
	mmap, err := syscall.Mmap(int(f.Fd()), 0, mapSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("mmap UIO device: %w", err)
	}

	return &UIODevice{
		file:    f,
		mmap:    mmap,
		mapSize: mapSize,
	}, nil
}

// ReadReg reads a 32-bit CSR register at the given byte offset from the
// memory-mapped AXI-Lite region.
func (d *UIODevice) ReadReg(offset uint32) (uint32, error) {
	if int(offset)+4 > d.mapSize {
		return 0, fmt.Errorf("UIO read reg 0x%X: offset exceeds map size (0x%X)", offset, d.mapSize)
	}
	return binary.LittleEndian.Uint32(d.mmap[offset : offset+4]), nil
}

// WriteReg writes a 32-bit value to a CSR register at the given byte offset.
func (d *UIODevice) WriteReg(offset, value uint32) error {
	if int(offset)+4 > d.mapSize {
		return fmt.Errorf("UIO write reg 0x%X: offset exceeds map size (0x%X)", offset, d.mapSize)
	}
	binary.LittleEndian.PutUint32(d.mmap[offset:offset+4], value)
	return nil
}

// WriteData copies data into the memory-mapped data buffer region at the
// given offset.
func (d *UIODevice) WriteData(offset uint32, data []byte) error {
	end := int(offset) + len(data)
	if end > d.mapSize {
		return fmt.Errorf("UIO write data at 0x%X len %d: exceeds map size", offset, len(data))
	}
	copy(d.mmap[offset:end], data)
	return nil
}

// ReadData reads data from the memory-mapped data buffer region.
func (d *UIODevice) ReadData(offset uint32, length int) ([]byte, error) {
	end := int(offset) + length
	if end > d.mapSize {
		return nil, fmt.Errorf("UIO read data at 0x%X len %d: exceeds map size", offset, length)
	}
	buf := make([]byte, length)
	copy(buf, d.mmap[offset:end])
	return buf, nil
}

// Transport returns "axi-uio".
func (d *UIODevice) Transport() string { return "axi-uio" }

// Close unmaps the device memory and closes the file descriptor.
func (d *UIODevice) Close() error {
	if err := syscall.Munmap(d.mmap); err != nil {
		d.file.Close()
		return fmt.Errorf("munmap: %w", err)
	}
	return d.file.Close()
}
