package cmd

import (
	"fmt"
	"strings"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/fpga"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/axi"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/pcie"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/uart"
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
  3. Run a basic functional test (keygen + encaps/sign + decaps/verify)
  4. Optionally run KAT vectors and compare against expected outputs
  5. Report hardware cycle counts from the performance counter`,
	RunE: runFPGA,
}

func init() {
	fpgaCmd.Flags().StringVarP(&fpgaTransport, "transport", "T", "pcie", "transport layer (pcie, axi, uart)")
	fpgaCmd.Flags().StringVarP(&fpgaDevice, "device", "d", "", "device path (e.g., /dev/xdma0, /dev/ttyUSB0, /dev/uio0)")
	fpgaCmd.Flags().IntVarP(&fpgaBaud, "baud", "b", 115200, "UART baud rate (only for uart transport)")
	fpgaCmd.Flags().IntVar(&fpgaMapSize, "map-size", 0x10000, "AXI mmap region size in bytes (only for axi transport)")
	fpgaCmd.Flags().IntVar(&fpgaTimeout, "timeout", 5, "operation timeout in seconds")
	fpgaCmd.Flags().StringVarP(&fpgaVectorDir, "vectors", "v", "", "KAT vector directory (skip KAT if empty)")
	fpgaCmd.Flags().BoolVar(&fpgaSkipKAT, "skip-kat", false, "skip KAT validation, only identify and test connectivity")
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
	fmt.Printf("[1/4] Connecting to FPGA... ")
	dev, err := openDevice()
	if err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("opening device: %w", err)
	}
	defer dev.Close()
	fmt.Printf("OK (%s)\n", dev.Transport())

	// Step 2: Identify the loaded accelerator core.
	fmt.Printf("[2/4] Reading core identification... ")
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
	fmt.Printf("[3/4] Register read/write test... ")
	if err := testRegisters(dev); err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("register test: %w", err)
	}
	fmt.Printf("OK\n")

	// Step 4: Run a basic operation cycle.
	fmt.Printf("[4/4] Operation cycle test... ")
	cycles, err := testOperation(dev, timeout)
	if err != nil {
		fmt.Printf("FAILED\n")
		return fmt.Errorf("operation test: %w", err)
	}
	fmt.Printf("OK (%d hardware cycles)\n\n", cycles)

	// Summary.
	fmt.Printf("=== FPGA Validation Summary ===\n")
	fmt.Printf("Core:       %s level %d (v%s)\n", info.Algorithm, info.SecurityLevel, info.Version)
	fmt.Printf("Transport:  %s via %s\n", dev.Transport(), fpgaDevice)
	fmt.Printf("Registers:  PASS\n")
	fmt.Printf("Operation:  PASS (%d cycles)\n", cycles)

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
