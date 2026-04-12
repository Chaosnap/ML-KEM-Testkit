package fpga

import (
	"fmt"
	"io"
	"time"
)

// CSR register offsets for the PQC accelerator control/status block.
// These offsets are fixed across all transports and vendors.
const (
	RegCTRL       = 0x00 // Control: start, reset, algorithm select.
	RegSTATUS     = 0x04 // Status: busy, done, error flags.
	RegALGID      = 0x08 // Algorithm ID (1=ML-KEM, 2=ML-DSA, 3=SLH-DSA).
	RegSecLevel   = 0x0C // Security level parameter.
	RegOpMode     = 0x10 // Operation mode (keygen/encaps/decaps).
	RegCycleCount = 0x14 // Cycle count latched on completion.
	RegVersion    = 0x18 // Core version (packed u32).
	RegErrorCode  = 0x1C // Error code from last operation.
	RegDataInAddr = 0x20 // Input data address/offset.
	RegDataInLen  = 0x24 // Input data length.
	RegDataOutAddr = 0x28 // Output data address/offset.
	RegDataOutLen  = 0x2C // Output data length.
)

// Control register bit positions.
const (
	CtrlStart = 1 << 0 // Start operation.
	CtrlReset = 1 << 1 // Reset core.
)

// Status register bit positions.
const (
	StatusBusy  = 1 << 0 // Core is processing.
	StatusDone  = 1 << 1 // Operation complete.
	StatusError = 1 << 2 // Error occurred.
)

// Operation modes for RegOpMode.
const (
	OpKeyGen  = 0 // Key generation.
	OpEncaps  = 1 // Encapsulation (KEM) or signing (DSA).
	OpDecaps  = 2 // Decapsulation (KEM) or verification (DSA).
)

// Algorithm IDs for RegALGID.
const (
	AlgMLKEM  = 1 // ML-KEM (FIPS 203).
	AlgMLDSA  = 2 // ML-DSA (FIPS 204).
	AlgSLHDSA = 3 // SLH-DSA (FIPS 205).
)

// Device represents a connection to an FPGA-based PQC accelerator.
// Implementations provide transport-specific access to the accelerator's
// control registers and data buffers.
type Device interface {
	io.Closer

	// ReadReg reads a 32-bit control/status register at the given offset.
	ReadReg(offset uint32) (uint32, error)

	// WriteReg writes a 32-bit value to a control register at the given offset.
	WriteReg(offset, value uint32) error

	// WriteData transfers input data (keys, messages, ciphertexts) to the
	// FPGA's data buffer. The offset is relative to the data region base.
	WriteData(offset uint32, data []byte) error

	// ReadData reads output data from the FPGA's data buffer.
	ReadData(offset uint32, length int) ([]byte, error)

	// Transport returns the transport type name (e.g., "pcie", "axi", "uart").
	Transport() string
}

// DeviceInfo holds identification information read from an FPGA accelerator
// core's CSR registers.
type DeviceInfo struct {
	// Algorithm is the PQC algorithm loaded on the FPGA.
	Algorithm string

	// AlgorithmID is the raw algorithm identifier from RegALGID.
	AlgorithmID uint32

	// SecurityLevel is the currently configured security parameter.
	SecurityLevel uint32

	// Version is the core version string (e.g., "1.0.0").
	Version string

	// VersionRaw is the packed version register value.
	VersionRaw uint32

	// Transport is the communication transport type.
	Transport string
}

// Identify reads the identification registers from an FPGA device and
// returns a DeviceInfo describing the loaded accelerator core.
func Identify(dev Device) (DeviceInfo, error) {
	algID, err := dev.ReadReg(RegALGID)
	if err != nil {
		return DeviceInfo{}, fmt.Errorf("reading algorithm ID: %w", err)
	}

	level, err := dev.ReadReg(RegSecLevel)
	if err != nil {
		return DeviceInfo{}, fmt.Errorf("reading security level: %w", err)
	}

	ver, err := dev.ReadReg(RegVersion)
	if err != nil {
		return DeviceInfo{}, fmt.Errorf("reading version: %w", err)
	}

	info := DeviceInfo{
		AlgorithmID:   algID,
		SecurityLevel: level,
		VersionRaw:    ver,
		Version:       fmt.Sprintf("%d.%d.%d", (ver>>16)&0xFF, (ver>>8)&0xFF, ver&0xFF),
		Transport:     dev.Transport(),
	}

	switch algID {
	case AlgMLKEM:
		info.Algorithm = "ML-KEM"
	case AlgMLDSA:
		info.Algorithm = "ML-DSA"
	case AlgSLHDSA:
		info.Algorithm = "SLH-DSA"
	default:
		info.Algorithm = fmt.Sprintf("unknown(%d)", algID)
	}

	return info, nil
}

// WaitDone polls the status register until the accelerator signals completion
// or the timeout expires. Returns the cycle count from the performance counter.
func WaitDone(dev Device, timeout time.Duration) (uint32, error) {
	deadline := time.Now().Add(timeout)

	for time.Now().Before(deadline) {
		status, err := dev.ReadReg(RegSTATUS)
		if err != nil {
			return 0, fmt.Errorf("reading status: %w", err)
		}

		if status&StatusError != 0 {
			errCode, _ := dev.ReadReg(RegErrorCode)
			return 0, fmt.Errorf("accelerator error: code=0x%04X", errCode)
		}

		if status&StatusDone != 0 {
			cycles, err := dev.ReadReg(RegCycleCount)
			if err != nil {
				return 0, fmt.Errorf("reading cycle count: %w", err)
			}
			return cycles, nil
		}

		time.Sleep(10 * time.Microsecond)
	}

	return 0, fmt.Errorf("timeout waiting for accelerator (%.1fs)", timeout.Seconds())
}
