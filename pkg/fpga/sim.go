package fpga

import (
	"crypto/rand"
	"fmt"
)

// SimDevice simulates an FPGA PQC accelerator in software for testing
// without real hardware. It implements the Device interface with a
// behavioral model that responds to register reads/writes and simulates
// operation completion with configurable cycle counts.
//
// Use this to:
//   - Run the full FPGA validation flow on any machine
//   - CI/CD testing without physical FPGA boards
//   - Develop and debug host-side tooling
//   - Validate KAT vector parsing before running on hardware
type SimDevice struct {
	regs     map[uint32]uint32
	data     map[uint32][]byte
	algID    uint32
	level    uint32
	version  uint32
	cycleCnt uint32
}

// SimConfig configures the simulated accelerator core.
type SimConfig struct {
	// AlgorithmID is the simulated algorithm (AlgMLKEM, AlgMLDSA, AlgSLHDSA).
	AlgorithmID uint32

	// SecurityLevel is the simulated security level.
	SecurityLevel uint32

	// Version is the simulated core version (packed major.minor.patch).
	Version uint32

	// CycleCount is the simulated hardware cycle count per operation.
	CycleCount uint32
}

// DefaultSimConfig returns a simulation configuration for an ML-KEM-768
// core at version 1.0.0 with 42,000 cycles per operation.
func DefaultSimConfig() SimConfig {
	return SimConfig{
		AlgorithmID:   AlgMLKEM,
		SecurityLevel: 768,
		Version:       0x010000, // v1.0.0
		CycleCount:    42000,
	}
}

// OpenSim creates a simulated FPGA device. No hardware required.
func OpenSim(cfg SimConfig) *SimDevice {
	d := &SimDevice{
		regs:     make(map[uint32]uint32),
		data:     make(map[uint32][]byte),
		algID:    cfg.AlgorithmID,
		level:    cfg.SecurityLevel,
		version:  cfg.Version,
		cycleCnt: cfg.CycleCount,
	}

	// Initialize read-only identification registers.
	d.regs[RegALGID] = cfg.AlgorithmID
	d.regs[RegSecLevel] = cfg.SecurityLevel
	d.regs[RegVersion] = cfg.Version
	d.regs[RegSTATUS] = 0
	d.regs[RegCycleCount] = 0
	d.regs[RegErrorCode] = 0
	d.regs[RegCTRL] = 0
	d.regs[RegOpMode] = 0

	return d
}

// ReadReg reads a simulated CSR register.
func (d *SimDevice) ReadReg(offset uint32) (uint32, error) {
	// Handle STATUS specially: return Done after Start was written.
	if offset == RegSTATUS {
		ctrl := d.regs[RegCTRL]
		if ctrl&CtrlStart != 0 {
			// Simulate instant completion.
			d.regs[RegCycleCount] = d.cycleCnt
			d.regs[RegCTRL] = 0 // Clear start.
			return StatusDone, nil
		}
		return 0, nil
	}

	v, ok := d.regs[offset]
	if !ok {
		return 0, fmt.Errorf("sim: uninitialized register 0x%02X", offset)
	}
	return v, nil
}

// WriteReg writes a simulated CSR register.
func (d *SimDevice) WriteReg(offset, value uint32) error {
	switch offset {
	case RegALGID, RegVersion:
		// Read-only registers: silently accept.
		return nil
	case RegCTRL:
		if value&CtrlReset != 0 {
			d.regs[RegSTATUS] = 0
			d.regs[RegCycleCount] = 0
		}
		d.regs[offset] = value
	default:
		d.regs[offset] = value
	}
	return nil
}

// WriteData writes data to the simulated data buffer.
func (d *SimDevice) WriteData(offset uint32, data []byte) error {
	d.data[offset] = append([]byte(nil), data...)
	return nil
}

// ReadData reads data from the simulated data buffer.
func (d *SimDevice) ReadData(offset uint32, length int) ([]byte, error) {
	if data, ok := d.data[offset]; ok && len(data) >= length {
		return data[:length], nil
	}
	buf := make([]byte, length)
	rand.Read(buf)
	return buf, nil
}

// Transport returns "sim".
func (d *SimDevice) Transport() string { return "sim" }

// Close is a no-op for the simulated device.
func (d *SimDevice) Close() error { return nil }
