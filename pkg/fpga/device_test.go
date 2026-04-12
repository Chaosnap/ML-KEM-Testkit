package fpga

import (
	"fmt"
	"testing"
	"time"
)

// simDevice implements Device for testing without real hardware.
type simDevice struct {
	regs map[uint32]uint32
	data map[uint32][]byte
}

func newSimDevice() *simDevice {
	return &simDevice{
		regs: map[uint32]uint32{
			RegALGID:      AlgMLKEM,
			RegSecLevel:   768,
			RegVersion:    0x010200, // v1.2.0
			RegSTATUS:     StatusDone,
			RegCycleCount: 42000,
		},
		data: make(map[uint32][]byte),
	}
}

func (d *simDevice) ReadReg(offset uint32) (uint32, error) {
	v, ok := d.regs[offset]
	if !ok {
		return 0, fmt.Errorf("register 0x%X not set", offset)
	}
	return v, nil
}

func (d *simDevice) WriteReg(offset, value uint32) error {
	d.regs[offset] = value
	return nil
}

func (d *simDevice) WriteData(offset uint32, data []byte) error {
	d.data[offset] = append([]byte(nil), data...)
	return nil
}

func (d *simDevice) ReadData(offset uint32, length int) ([]byte, error) {
	data, ok := d.data[offset]
	if !ok {
		return make([]byte, length), nil
	}
	if len(data) < length {
		padded := make([]byte, length)
		copy(padded, data)
		return padded, nil
	}
	return data[:length], nil
}

func (d *simDevice) Transport() string { return "sim" }
func (d *simDevice) Close() error      { return nil }

func TestIdentify(t *testing.T) {
	dev := newSimDevice()

	info, err := Identify(dev)
	if err != nil {
		t.Fatalf("Identify: %v", err)
	}

	if info.Algorithm != "ML-KEM" {
		t.Errorf("algorithm: got %s, want ML-KEM", info.Algorithm)
	}
	if info.AlgorithmID != AlgMLKEM {
		t.Errorf("algorithm ID: got %d, want %d", info.AlgorithmID, AlgMLKEM)
	}
	if info.SecurityLevel != 768 {
		t.Errorf("security level: got %d, want 768", info.SecurityLevel)
	}
	if info.Version != "1.2.0" {
		t.Errorf("version: got %s, want 1.2.0", info.Version)
	}
	if info.Transport != "sim" {
		t.Errorf("transport: got %s, want sim", info.Transport)
	}
}

func TestIdentifyAllAlgorithms(t *testing.T) {
	tests := []struct {
		algID uint32
		want  string
	}{
		{AlgMLKEM, "ML-KEM"},
		{AlgMLDSA, "ML-DSA"},
		{AlgSLHDSA, "SLH-DSA"},
		{99, "unknown(99)"},
	}

	for _, tt := range tests {
		dev := newSimDevice()
		dev.regs[RegALGID] = tt.algID

		info, err := Identify(dev)
		if err != nil {
			t.Fatalf("Identify (algID=%d): %v", tt.algID, err)
		}
		if info.Algorithm != tt.want {
			t.Errorf("algID %d: got %s, want %s", tt.algID, info.Algorithm, tt.want)
		}
	}
}

func TestWaitDone(t *testing.T) {
	dev := newSimDevice()
	dev.regs[RegSTATUS] = StatusDone
	dev.regs[RegCycleCount] = 12345

	cycles, err := WaitDone(dev, 1*time.Second)
	if err != nil {
		t.Fatalf("WaitDone: %v", err)
	}
	if cycles != 12345 {
		t.Errorf("cycles: got %d, want 12345", cycles)
	}
}

func TestWaitDoneError(t *testing.T) {
	dev := newSimDevice()
	dev.regs[RegSTATUS] = StatusError
	dev.regs[RegErrorCode] = 0x0042

	_, err := WaitDone(dev, 1*time.Second)
	if err == nil {
		t.Fatal("expected error for StatusError")
	}
}

func TestWaitDoneTimeout(t *testing.T) {
	dev := newSimDevice()
	dev.regs[RegSTATUS] = StatusBusy // Never becomes done.

	_, err := WaitDone(dev, 10*time.Millisecond)
	if err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestCSRConstants(t *testing.T) {
	// Verify register offsets don't overlap and are 4-byte aligned.
	offsets := []uint32{
		RegCTRL, RegSTATUS, RegALGID, RegSecLevel,
		RegOpMode, RegCycleCount, RegVersion, RegErrorCode,
		RegDataInAddr, RegDataInLen, RegDataOutAddr, RegDataOutLen,
	}

	seen := make(map[uint32]bool)
	for _, off := range offsets {
		if off%4 != 0 {
			t.Errorf("register 0x%X not 4-byte aligned", off)
		}
		if seen[off] {
			t.Errorf("duplicate register offset 0x%X", off)
		}
		seen[off] = true
	}
}

func TestMockDataReadWrite(t *testing.T) {
	dev := newSimDevice()

	testData := []byte{0xDE, 0xAD, 0xBE, 0xEF}
	if err := dev.WriteData(0x1000, testData); err != nil {
		t.Fatalf("WriteData: %v", err)
	}

	got, err := dev.ReadData(0x1000, 4)
	if err != nil {
		t.Fatalf("ReadData: %v", err)
	}

	for i := range testData {
		if got[i] != testData[i] {
			t.Fatalf("data mismatch at byte %d: got 0x%02X, want 0x%02X", i, got[i], testData[i])
		}
	}
}
