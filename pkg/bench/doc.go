// Package bench provides performance benchmarking for PQC implementations.
//
// It measures keygen, encapsulation/signing, and decapsulation/verification
// latency across multiple iterations and reports median, p99, and throughput
// statistics. Benchmarks can target software reference implementations or
// FPGA hardware (reading cycle counts from the accelerator's performance
// counters).
//
// Usage:
//
//	suite := algorithms.NewMLKEM()
//	results, err := bench.Run(suite, 768, 1000)
//	for _, r := range results {
//	    fmt.Printf("%s: median=%s ops/sec=%.0f\n", r.Operation, r.Median, r.OpsPerSec)
//	}
package bench
