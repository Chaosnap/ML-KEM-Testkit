// Package main provides the pqc-testkit CLI for testing and benchmarking
// post-quantum cryptography implementations across software and FPGA targets.
package main

import (
	"fmt"
	"os"

	"github.com/akhilesharora/pqc-testkit/cmd/pqc-testkit/cmd"
)

func main() {
	if err := cmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}
