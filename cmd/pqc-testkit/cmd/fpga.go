package cmd

import (
	"bytes"
	"crypto/rand"
	"fmt"
	"strings"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/fpga"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/axi"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/pcie"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/uart"
	"github.com/cloudflare/circl/kem"
	"github.com/cloudflare/circl/kem/mlkem/mlkem1024"
	"github.com/cloudflare/circl/kem/mlkem/mlkem512"
	"github.com/cloudflare/circl/kem/mlkem/mlkem768"
	"github.com/spf13/cobra"
)

var (
	fpgaTransport  string
	fpgaDevice     string
	fpgaBaud       int
	fpgaMapSize    int
	fpgaTimeout    int
	fpgaVectorDir  string
	fpgaSkipKAT    bool
)

var fpgaCmd = &cobra.Command{
	Use:   "fpga",
	Short: "Run tests against FPGA hardware target",
	Long: `Execute KAT validation and benchmarks against a live FPGA target.

Communicates with the FPGA via PCIe (XDMA/Avalon), AXI (Zynq SoC),
or UART (development boards). The FPGA must be programmed with a
compatible PQC accelerator bitstream.

The command will:
  1. Open the transport and connect to the FPGA
  2. Read CSR registers to identify the loaded algorithm and version
  3. Check register read/write and a data-buffer write/read loopback
  4. Run an operation start/done cycle
  5. For ML-KEM cores, run KeyGen/Encaps/Decaps (and implicit rejection)
     at every level on the FPGA and compare byte-for-byte against the
     software reference (skip with --skip-kat)
  6. Report hardware cycle counts from the performance counter`,
	RunE: runFPGA,
}

func init() {
	fpgaCmd.Flags().StringVarP(&fpgaTransport, "transport", "T", "pcie", "transport layer (pcie, axi, uart)")
	fpgaCmd.Flags().StringVarP(&fpgaDevice, "device", "d", "", "device path (e.g., /dev/xdma0, /dev/ttyUSB0, /dev/uio0)")
	fpgaCmd.Flags().IntVarP(&fpgaBaud, "baud", "b", 115200, "UART baud rate (only for uart transport)")
	fpgaCmd.Flags().IntVar(&fpgaMapSize, "map-size", 0x10000, "AXI mmap region size in bytes (only for axi transport)")
	fpgaCmd.Flags().IntVar(&fpgaTimeout, "timeout", 5, "operation timeout in seconds")
	fpgaCmd.Flags().StringVarP(&fpgaVectorDir, "vectors", "v", "", "KAT vector directory (skip KAT if empty)")
	fpgaCmd.Flags().BoolVar(&fpgaSkipKAT, "skip-kat", false, "skip ML-KEM known-answer tests, only identify and test connectivity")
	rootCmd.AddCommand(fpgaCmd)
}

// openDevice opens an FPGA device using the specified transport.
// Use --transport sim to simulate without hardware.
func openDevice() (fpga.Device, error) {
	switch strings.ToLower(fpgaTransport) {
	case "pcie", "xdma":
		return pcie.OpenXDMA(fpgaDevice)
	case "axi", "uio":
		return axi.OpenUIO(fpgaDevice, fpgaMapSize)
	case "uart", "serial":
		return uart.Open(fpgaDevice, fpgaBaud)
	case "sim":
		return fpga.OpenSim(fpga.DefaultSimConfig()), nil
	default:
		return nil, fmt.Errorf("unknown transport %q (supported: pcie, axi, uart, sim)", fpgaTransport)
	}
}

func runFPGA(cmd *cobra.Command, args []string) error {
	if fpgaDevice == "" && fpgaTransport != "sim" {
		return fmt.Errorf("--device is required (or use --transport sim to simulate)\n\n" +
			"Examples:\n" +
			"  PCIe (XDMA):   --device /dev/xdma0\n" +
			"  AXI (Zynq):    --device /dev/uio0\n" +
			"  UART (serial): --device /dev/ttyUSB0\n" +
			"  Simulation:    --transport sim")
	}

	timeout := time.Duration(fpgaTimeout) * time.Second

	fmt.Printf("PQC Test Kit - FPGA Target\n")
	fmt.Printf("Transport: %s\n", fpgaTransport)
	fmt.Printf("Device:    %s\n", fpgaDevice)
	fmt.Printf("Timeout:   %s\n\n", timeout)

	// Step 1: Open the transport.
	fmt.Printf("[1/6] Connecting to FPGA... ")
	dev, err := openDevice()
	if err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("opening device: %w", err)
	}
	defer dev.Close()
	fmt.Printf("OK (%s)\n", dev.Transport())

	// Step 2: Identify the loaded accelerator core.
	fmt.Printf("[2/6] Reading core identification... ")
	info, err := fpga.Identify(dev)
	if err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("identifying core: %w", err)
	}
	fmt.Printf("OK\n")
	fmt.Printf("       Algorithm:      %s (ID=%d)\n", info.Algorithm, info.AlgorithmID)
	fmt.Printf("       Security Level: %d\n", info.SecurityLevel)
	fmt.Printf("       Core Version:   %s\n", info.Version)
	fmt.Printf("       Transport:      %s\n\n", info.Transport)

	// Step 3: Functional test - write/read registers.
	fmt.Printf("[3/6] Register read/write test... ")
	if err := testRegisters(dev); err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("register test: %w", err)
	}
	fmt.Printf("OK\n")

	// Step 4: Data buffer loopback.
	fmt.Printf("[4/6] Data buffer loopback test... ")
	if err := testBufferLoopback(dev); err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("buffer loopback: %w", err)
	}
	fmt.Printf("OK\n")

	// Step 5: Run a basic operation cycle.
	fmt.Printf("[5/6] Operation cycle test... ")
	cycles, err := testOperation(dev, timeout)
	if err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("operation test: %w", err)
	}
	fmt.Printf("OK (%d hardware cycles)\n", cycles)

	// Step 6: Known-answer tests against the software reference.
	katResult := "SKIPPED"
	if fpgaSkipKAT || info.AlgorithmID != fpga.AlgMLKEM {
		fmt.Printf("[6/6] ML-KEM known-answer tests... skipped\n\n")
	} else {
		fmt.Printf("[6/6] ML-KEM known-answer tests (FPGA vs software, byte-for-byte):\n")
		if err := testMLKEMKAT(dev, timeout); err != nil {
			return fmt.Errorf("ML-KEM KAT: %w", err)
		}
		katResult = "PASS"
		fmt.Println()
	}

	// Summary.
	fmt.Printf("=== FPGA Validation Summary ===\n")
	fmt.Printf("Core:       %s level %d (v%s)\n", info.Algorithm, info.SecurityLevel, info.Version)
	fmt.Printf("Transport:  %s via %s\n", dev.Transport(), fpgaDevice)
	fmt.Printf("Registers:  PASS\n")
	fmt.Printf("Buffer:     PASS\n")
	fmt.Printf("Operation:  PASS (%d cycles)\n", cycles)
	fmt.Printf("ML-KEM KAT: %s\n", katResult)

	if fpgaVectorDir != "" && !fpgaSkipKAT {
		fmt.Printf("\nKAT vector validation against FPGA is planned but not yet wired.\n")
		fmt.Printf("Vectors directory: %s\n", fpgaVectorDir)
		fmt.Printf("This will send each KAT input to the FPGA via %s,\n", dev.Transport())
		fmt.Printf("read back the output, and compare against expected values.\n")
	}

	return nil
}

// testRegisters performs a basic register read/write sanity check.
// Writes to the SEC_LEVEL register and reads it back.
func testRegisters(dev fpga.Device) error {
	// Read current security level.
	original, err := dev.ReadReg(fpga.RegSecLevel)
	if err != nil {
		return fmt.Errorf("reading SEC_LEVEL: %w", err)
	}

	// Write a test value.
	testVal := uint32(768)
	if err := dev.WriteReg(fpga.RegSecLevel, testVal); err != nil {
		return fmt.Errorf("writing SEC_LEVEL: %w", err)
	}

	// Read back and verify.
	readback, err := dev.ReadReg(fpga.RegSecLevel)
	if err != nil {
		return fmt.Errorf("reading back SEC_LEVEL: %w", err)
	}
	if readback != testVal {
		return fmt.Errorf("register readback mismatch: wrote 0x%X, read 0x%X", testVal, readback)
	}

	// Restore original value.
	if err := dev.WriteReg(fpga.RegSecLevel, original); err != nil {
		return fmt.Errorf("restoring SEC_LEVEL: %w", err)
	}

	return nil
}

// testOperation runs a single operation cycle: reset, start, wait for done.
func testOperation(dev fpga.Device, timeout time.Duration) (uint32, error) {
	// Reset the core.
	if err := dev.WriteReg(fpga.RegCTRL, fpga.CtrlReset); err != nil {
		return 0, fmt.Errorf("resetting core: %w", err)
	}

	// Clear reset.
	if err := dev.WriteReg(fpga.RegCTRL, 0); err != nil {
		return 0, fmt.Errorf("clearing reset: %w", err)
	}

	// Set operation mode to keygen (simplest operation).
	if err := dev.WriteReg(fpga.RegOpMode, fpga.OpKeyGen); err != nil {
		return 0, fmt.Errorf("setting op mode: %w", err)
	}

	// Start the operation.
	if err := dev.WriteReg(fpga.RegCTRL, fpga.CtrlStart); err != nil {
		return 0, fmt.Errorf("starting operation: %w", err)
	}

	// Wait for completion and read cycle count.
	cycles, err := fpga.WaitDone(dev, timeout)
	if err != nil {
		return 0, fmt.Errorf("waiting for done: %w", err)
	}

	return cycles, nil
}

// FPGA data buffer layout used by the ML-KEM core (reset values of
// DATA_IN_ADDR / DATA_OUT_ADDR; see hdl/core/pqc_data_buffer.sv).
const (
	mlkemInOffset  = 0x0000
	mlkemOutOffset = 0x1800
)

// testBufferLoopback writes a random pattern to the data buffer at an
// unaligned offset and reads it back.
func testBufferLoopback(dev fpga.Device) error {
	pattern := make([]byte, 600)
	if _, err := rand.Read(pattern); err != nil {
		return err
	}
	const offset = mlkemOutOffset + 1
	if err := dev.WriteData(offset, pattern); err != nil {
		return err
	}
	got, err := dev.ReadData(offset, len(pattern))
	if err != nil {
		return err
	}
	if !bytes.Equal(got, pattern) {
		return fmt.Errorf("readback mismatch at offset 0x%X", offset)
	}
	return nil
}

// runMLKEMOp executes one ML-KEM operation on the FPGA: it writes the
// input to the data buffer, starts the core and returns DATA_OUT_LEN bytes
// of output.
func runMLKEMOp(dev fpga.Device, level, op int, input []byte, timeout time.Duration) ([]byte, uint32, error) {
	if err := dev.WriteData(mlkemInOffset, input); err != nil {
		return nil, 0, err
	}
	for _, w := range []struct{ reg, val uint32 }{
		{fpga.RegDataInAddr, mlkemInOffset},
		{fpga.RegDataInLen, uint32(len(input))},
		{fpga.RegDataOutAddr, mlkemOutOffset},
		{fpga.RegSecLevel, uint32(level)},
		{fpga.RegOpMode, uint32(op)},
		{fpga.RegCTRL, fpga.CtrlStart},
	} {
		if err := dev.WriteReg(w.reg, w.val); err != nil {
			return nil, 0, err
		}
	}
	cycles, err := fpga.WaitDone(dev, timeout)
	if err != nil {
		return nil, 0, err
	}
	outLen, err := dev.ReadReg(fpga.RegDataOutLen)
	if err != nil {
		return nil, 0, err
	}
	out, err := dev.ReadData(mlkemOutOffset, int(outLen))
	return out, cycles, err
}

// testMLKEMKAT runs KeyGen, Encaps, Decaps and Decaps of a corrupted
// ciphertext (implicit rejection) on the FPGA for every ML-KEM level with
// fresh random seeds, and compares every output byte against circl.
func testMLKEMKAT(dev fpga.Device, timeout time.Duration) error {
	schemes := []struct {
		level  int
		scheme kem.Scheme
	}{
		{512, mlkem512.Scheme()},
		{768, mlkem768.Scheme()},
		{1024, mlkem1024.Scheme()},
	}
	for _, sc := range schemes {
		seed := make([]byte, sc.scheme.SeedSize())
		m := make([]byte, sc.scheme.EncapsulationSeedSize())
		if _, err := rand.Read(seed); err != nil {
			return err
		}
		if _, err := rand.Read(m); err != nil {
			return err
		}
		pk, sk := sc.scheme.DeriveKeyPair(seed)
		ek, _ := pk.MarshalBinary()
		dk, _ := sk.MarshalBinary()
		ct, ss, err := sc.scheme.EncapsulateDeterministically(pk, m)
		if err != nil {
			return err
		}
		badCT := append([]byte(nil), ct...)
		badCT[0] ^= 0x01
		ssReject, err := sc.scheme.Decapsulate(sk, badCT)
		if err != nil {
			return err
		}

		cases := []struct {
			name   string
			op     int
			input  []byte
			expect []byte
		}{
			{"KeyGen", fpga.OpKeyGen, cat(seed), cat(ek, dk)},
			{"Encaps", fpga.OpEncaps, cat(ek, m), cat(ct, ss)},
			{"Decaps", fpga.OpDecaps, cat(dk, ct), ss},
			{"Decaps (implicit reject)", fpga.OpDecaps, cat(dk, badCT), ssReject},
		}
		for _, c := range cases {
			fmt.Printf("       ML-KEM-%-4d %-24s ", sc.level, c.name)
			got, cycles, err := runMLKEMOp(dev, sc.level, c.op, c.input, timeout)
			if err != nil {
				fmt.Printf("FAILED\n")
				return fmt.Errorf("ML-KEM-%d %s: %w", sc.level, c.name, err)
			}
			if !bytes.Equal(got, c.expect) {
				fmt.Printf("MISMATCH\n")
				return fmt.Errorf("ML-KEM-%d %s: output differs from software reference (%s)",
					sc.level, c.name, firstDiff(got, c.expect))
			}
			fmt.Printf("PASS (%d bytes, %d cycles)\n", len(got), cycles)
		}
	}
	return nil
}

// cat concatenates byte slices into a new slice.
func cat(parts ...[]byte) []byte {
	return bytes.Join(parts, nil)
}

// firstDiff describes the first differing byte of two slices.
func firstDiff(got, want []byte) string {
	if len(got) != len(want) {
		return fmt.Sprintf("length %d, want %d", len(got), len(want))
	}
	for i := range got {
		if got[i] != want[i] {
			return fmt.Sprintf("byte %d: 0x%02X, want 0x%02X", i, got[i], want[i])
		}
	}
	return "equal"
}
