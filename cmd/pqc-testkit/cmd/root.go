// Package cmd implements the CLI commands for pqc-testkit.
package cmd

import (
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "pqc-testkit",
	Short: "Post-Quantum Cryptography test kit for FPGA validation",
	Long: `pqc-testkit is a comprehensive test and benchmarking framework for
post-quantum cryptography (PQC) implementations targeting FPGA hardware.

Supports ML-KEM (Kyber), ML-DSA (Dilithium), and SLH-DSA (SPHINCS+)
across Xilinx, Intel/Altera, and Logic Fruit FPGA platforms.

The tool provides:
  - NIST KAT (Known Answer Test) vector validation
  - Software reference implementation benchmarks
  - FPGA-in-the-loop testing via PCIe, AXI, or UART
  - Side-channel analysis test pattern generation
  - Cross-vendor performance comparison`,
}

// Execute runs the root command.
func Execute() error {
	return rootCmd.Execute()
}
