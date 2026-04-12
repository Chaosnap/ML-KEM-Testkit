package bench

import (
	"fmt"
	"sort"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/algorithms"
)

// Result holds the benchmark results for a single operation type.
type Result struct {
	// Operation is the name of the benchmarked operation (e.g., "keygen", "encaps").
	Operation string

	// Iterations is the number of times the operation was executed.
	Iterations int

	// Median is the median latency across all iterations.
	Median time.Duration

	// P99 is the 99th percentile latency.
	P99 time.Duration

	// Min is the minimum observed latency.
	Min time.Duration

	// Max is the maximum observed latency.
	Max time.Duration

	// OpsPerSec is the throughput calculated from median latency.
	OpsPerSec float64

	// TotalTime is the wall-clock time for all iterations.
	TotalTime time.Duration
}

// Run executes benchmarks for all operations of the given algorithm suite
// at the specified security level. Returns results for each operation type.
func Run(suite algorithms.Suite, level int, iterations int) ([]Result, error) {
	if iterations < 1 {
		return nil, fmt.Errorf("iterations must be >= 1, got %d", iterations)
	}

	var results []Result

	// Benchmark keygen.
	keygenResult, err := benchKeygen(suite, level, iterations)
	if err != nil {
		return nil, fmt.Errorf("keygen benchmark: %w", err)
	}
	results = append(results, keygenResult)

	// Benchmark type-specific operations.
	switch s := suite.(type) {
	case algorithms.KEMSuite:
		encapsResult, err := benchEncaps(s, level, iterations)
		if err != nil {
			return nil, fmt.Errorf("encaps benchmark: %w", err)
		}
		results = append(results, encapsResult)

		decapsResult, err := benchDecaps(s, level, iterations)
		if err != nil {
			return nil, fmt.Errorf("decaps benchmark: %w", err)
		}
		results = append(results, decapsResult)

	case algorithms.SignSuite:
		signResult, err := benchSign(s, level, iterations)
		if err != nil {
			return nil, fmt.Errorf("sign benchmark: %w", err)
		}
		results = append(results, signResult)

		verifyResult, err := benchVerify(s, level, iterations)
		if err != nil {
			return nil, fmt.Errorf("verify benchmark: %w", err)
		}
		results = append(results, verifyResult)
	}

	return results, nil
}

func benchKeygen(suite algorithms.Suite, level, iterations int) (Result, error) {
	durations := make([]time.Duration, iterations)
	start := time.Now()

	for i := range iterations {
		t := time.Now()
		_, _, err := suite.KeyGen(level)
		durations[i] = time.Since(t)
		if err != nil {
			return Result{}, err
		}
	}

	return computeResult("keygen", durations, time.Since(start)), nil
}

func benchEncaps(suite algorithms.KEMSuite, level, iterations int) (Result, error) {
	// Generate a key pair for encapsulation benchmarks.
	pk, _, err := suite.KeyGen(level)
	if err != nil {
		return Result{}, err
	}

	durations := make([]time.Duration, iterations)
	start := time.Now()

	for i := range iterations {
		t := time.Now()
		_, _, err := suite.Encapsulate(level, pk)
		durations[i] = time.Since(t)
		if err != nil {
			return Result{}, err
		}
	}

	return computeResult("encaps", durations, time.Since(start)), nil
}

func benchDecaps(suite algorithms.KEMSuite, level, iterations int) (Result, error) {
	pk, sk, err := suite.KeyGen(level)
	if err != nil {
		return Result{}, err
	}

	ct, _, err := suite.Encapsulate(level, pk)
	if err != nil {
		return Result{}, err
	}

	durations := make([]time.Duration, iterations)
	start := time.Now()

	for i := range iterations {
		t := time.Now()
		_, err := suite.Decapsulate(level, sk, ct)
		durations[i] = time.Since(t)
		if err != nil {
			return Result{}, err
		}
	}

	return computeResult("decaps", durations, time.Since(start)), nil
}

func benchSign(suite algorithms.SignSuite, level, iterations int) (Result, error) {
	_, sk, err := suite.KeyGen(level)
	if err != nil {
		return Result{}, err
	}

	msg := []byte("PQC test kit benchmark message for performance measurement")

	durations := make([]time.Duration, iterations)
	start := time.Now()

	for i := range iterations {
		t := time.Now()
		_, err := suite.Sign(level, sk, msg)
		durations[i] = time.Since(t)
		if err != nil {
			return Result{}, err
		}
	}

	return computeResult("sign", durations, time.Since(start)), nil
}

func benchVerify(suite algorithms.SignSuite, level, iterations int) (Result, error) {
	pk, sk, err := suite.KeyGen(level)
	if err != nil {
		return Result{}, err
	}

	msg := []byte("PQC test kit benchmark message for performance measurement")
	sig, err := suite.Sign(level, sk, msg)
	if err != nil {
		return Result{}, err
	}

	durations := make([]time.Duration, iterations)
	start := time.Now()

	for i := range iterations {
		t := time.Now()
		_, err := suite.Verify(level, pk, msg, sig)
		durations[i] = time.Since(t)
		if err != nil {
			return Result{}, err
		}
	}

	return computeResult("verify", durations, time.Since(start)), nil
}

func computeResult(op string, durations []time.Duration, total time.Duration) Result {
	sort.Slice(durations, func(i, j int) bool {
		return durations[i] < durations[j]
	})

	n := len(durations)
	median := durations[n/2]
	p99idx := int(float64(n) * 0.99)
	if p99idx >= n {
		p99idx = n - 1
	}

	return Result{
		Operation:  op,
		Iterations: n,
		Median:     median,
		P99:        durations[p99idx],
		Min:        durations[0],
		Max:        durations[n-1],
		OpsPerSec:  float64(time.Second) / float64(median),
		TotalTime:  total,
	}
}
