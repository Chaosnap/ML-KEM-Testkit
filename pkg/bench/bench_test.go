package bench

import (
	"testing"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/algorithms"
)

func TestRunMLKEM(t *testing.T) {
	suite := algorithms.NewMLKEM()
	results, err := Run(suite, 768, 10)
	if err != nil {
		t.Fatalf("Run ML-KEM-768: %v", err)
	}

	// ML-KEM should produce keygen, encaps, decaps results.
	if len(results) != 3 {
		t.Fatalf("expected 3 results, got %d", len(results))
	}

	ops := map[string]bool{"keygen": false, "encaps": false, "decaps": false}
	for _, r := range results {
		ops[r.Operation] = true
		if r.Iterations != 10 {
			t.Errorf("%s: iterations = %d, want 10", r.Operation, r.Iterations)
		}
		if r.Median <= 0 {
			t.Errorf("%s: median should be positive, got %v", r.Operation, r.Median)
		}
		if r.OpsPerSec <= 0 {
			t.Errorf("%s: ops/sec should be positive, got %f", r.Operation, r.OpsPerSec)
		}
		if r.Min > r.Median || r.Median > r.Max {
			t.Errorf("%s: min/median/max ordering violated: %v/%v/%v", r.Operation, r.Min, r.Median, r.Max)
		}
	}

	for op, found := range ops {
		if !found {
			t.Errorf("missing result for operation %s", op)
		}
	}
}

func TestRunMLDSA(t *testing.T) {
	suite := algorithms.NewMLDSA()
	results, err := Run(suite, 44, 5)
	if err != nil {
		t.Fatalf("Run ML-DSA-44: %v", err)
	}

	// ML-DSA should produce keygen, sign, verify results.
	if len(results) != 3 {
		t.Fatalf("expected 3 results, got %d", len(results))
	}

	ops := map[string]bool{"keygen": false, "sign": false, "verify": false}
	for _, r := range results {
		ops[r.Operation] = true
	}
	for op, found := range ops {
		if !found {
			t.Errorf("missing result for operation %s", op)
		}
	}
}

func TestRunInvalidIterations(t *testing.T) {
	suite := algorithms.NewMLKEM()
	_, err := Run(suite, 768, 0)
	if err == nil {
		t.Error("expected error for 0 iterations")
	}
}

func TestComputeResult(t *testing.T) {
	durations := []time.Duration{
		10 * time.Microsecond,
		20 * time.Microsecond,
		30 * time.Microsecond,
		40 * time.Microsecond,
		50 * time.Microsecond,
	}

	r := computeResult("test", durations, 150*time.Microsecond)

	if r.Operation != "test" {
		t.Errorf("operation: got %s, want test", r.Operation)
	}
	if r.Iterations != 5 {
		t.Errorf("iterations: got %d, want 5", r.Iterations)
	}
	if r.Median != 30*time.Microsecond {
		t.Errorf("median: got %v, want 30us", r.Median)
	}
	if r.Min != 10*time.Microsecond {
		t.Errorf("min: got %v, want 10us", r.Min)
	}
	if r.Max != 50*time.Microsecond {
		t.Errorf("max: got %v, want 50us", r.Max)
	}
}
