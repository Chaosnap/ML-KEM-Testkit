package cmd

import (
	"fmt"
	"strings"

	"github.com/akhilesharora/pqc-testkit/pkg/algorithms"
	"github.com/akhilesharora/pqc-testkit/pkg/bench"
	"github.com/spf13/cobra"
)

var (
	benchAlgorithm  string
	benchIterations int
	benchTarget     string
)

var benchCmd = &cobra.Command{
	Use:   "bench",
	Short: "Benchmark PQC algorithm performance",
	Long: `Run performance benchmarks for PQC algorithms in software or against
FPGA hardware. Measures keygen, encapsulation/signing, and
decapsulation/verification across all security levels.

Results include median latency, throughput (ops/sec), and percentile
distributions.`,
	RunE: runBench,
}

func init() {
	benchCmd.Flags().StringVarP(&benchAlgorithm, "algorithm", "a", "ml-kem", "algorithm to benchmark (ml-kem, ml-dsa, slh-dsa)")
	benchCmd.Flags().IntVarP(&benchIterations, "iterations", "n", 1000, "number of iterations per operation")
	benchCmd.Flags().StringVarP(&benchTarget, "target", "t", "sw", "benchmark target (sw, fpga)")
	rootCmd.AddCommand(benchCmd)
}

func runBench(cmd *cobra.Command, args []string) error {
	algo := strings.ToLower(benchAlgorithm)

	suite, err := algorithms.NewSuite(algo)
	if err != nil {
		return fmt.Errorf("unsupported algorithm %q: %w", algo, err)
	}

	fmt.Printf("PQC Test Kit — Benchmark\n")
	fmt.Printf("Algorithm:  %s\n", suite.Name())
	fmt.Printf("Target:     %s\n", benchTarget)
	fmt.Printf("Iterations: %d\n\n", benchIterations)

	for _, level := range suite.Levels() {
		fmt.Printf("--- Level %d ---\n", level)
		results, err := bench.Run(suite, level, benchIterations)
		if err != nil {
			fmt.Printf("  [ERROR] %v\n\n", err)
			continue
		}
		for _, r := range results {
			fmt.Printf("  %-12s  median=%-12s  p99=%-12s  ops/sec=%.0f\n",
				r.Operation, r.Median, r.P99, r.OpsPerSec)
		}
		fmt.Println()
	}

	return nil
}
