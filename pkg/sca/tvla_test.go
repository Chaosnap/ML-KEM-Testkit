package sca

import (
	"math"
	"testing"
)

func TestGenerateTVLA(t *testing.T) {
	cfg := TVLAConfig{
		NumTraces:   100,
		KeySize:     32,
		MessageSize: 32,
	}

	vectors, err := GenerateTVLA(cfg)
	if err != nil {
		t.Fatalf("GenerateTVLA: %v", err)
	}

	if len(vectors) != 100 {
		t.Fatalf("expected 100 vectors, got %d", len(vectors))
	}

	// Count classes.
	fixed, random := 0, 0
	for _, v := range vectors {
		switch v.Class {
		case 0:
			fixed++
		case 1:
			random++
		default:
			t.Errorf("unexpected class: %d", v.Class)
		}
		if len(v.Key) != 32 {
			t.Errorf("key size: got %d, want 32", len(v.Key))
		}
		if len(v.Input) != 32 {
			t.Errorf("input size: got %d, want 32", len(v.Input))
		}
	}

	if fixed != 50 || random != 50 {
		t.Errorf("class split: %d fixed, %d random, want 50/50", fixed, random)
	}

	// All fixed-class vectors should have the same key and input.
	var fixedKey, fixedInput []byte
	for _, v := range vectors {
		if v.Class == 0 {
			if fixedKey == nil {
				fixedKey = v.Key
				fixedInput = v.Input
			} else {
				for i := range fixedKey {
					if v.Key[i] != fixedKey[i] {
						t.Fatal("fixed-class keys differ")
					}
				}
				for i := range fixedInput {
					if v.Input[i] != fixedInput[i] {
						t.Fatal("fixed-class inputs differ")
					}
				}
			}
		}
	}
}

func TestGenerateTVLAWithFixedValues(t *testing.T) {
	fixedKey := make([]byte, 16)
	fixedMsg := make([]byte, 16)
	for i := range fixedKey {
		fixedKey[i] = byte(i)
		fixedMsg[i] = byte(i + 16)
	}

	cfg := TVLAConfig{
		FixedKey:     fixedKey,
		FixedMessage: fixedMsg,
		NumTraces:    10,
		KeySize:      16,
		MessageSize:  16,
	}

	vectors, err := GenerateTVLA(cfg)
	if err != nil {
		t.Fatalf("GenerateTVLA: %v", err)
	}

	// First vector (class 0) should use our fixed values.
	if vectors[0].Class != 0 {
		t.Error("first vector should be fixed class")
	}
	for i := range fixedKey {
		if vectors[0].Key[i] != fixedKey[i] {
			t.Fatal("fixed key not used")
		}
	}
}

func TestGenerateTVLAInvalid(t *testing.T) {
	_, err := GenerateTVLA(TVLAConfig{NumTraces: 1, KeySize: 32, MessageSize: 32})
	if err == nil {
		t.Error("expected error for NumTraces < 2")
	}

	_, err = GenerateTVLA(TVLAConfig{NumTraces: 10, KeySize: 0, MessageSize: 32})
	if err == nil {
		t.Error("expected error for KeySize = 0")
	}
}

func TestWelchTTest(t *testing.T) {
	// Create traces where fixed class has a clear offset at sample 5.
	// Add small noise so variance is non-zero (avoids 0/0 in t-statistic).
	numSamples := 10
	fixed := make([][]float64, 50)
	random := make([][]float64, 50)

	for i := range fixed {
		trace := make([]float64, numSamples)
		for j := range trace {
			trace[j] = 1.0 + float64(i)*0.001 // Small noise.
		}
		trace[5] = 10.0 + float64(i)*0.001 // Leakage point.
		fixed[i] = trace
	}

	for i := range random {
		trace := make([]float64, numSamples)
		for j := range trace {
			trace[j] = 1.0 + float64(i)*0.001
		}
		trace[5] = 1.0 + float64(i)*0.001 // No leakage.
		random[i] = trace
	}

	result, err := WelchTTest(fixed, random)
	if err != nil {
		t.Fatalf("WelchTTest: %v", err)
	}

	if len(result.TValues) != numSamples {
		t.Fatalf("expected %d t-values, got %d", numSamples, len(result.TValues))
	}

	// Sample 5 should have the highest |t|.
	if result.MaxAbsTIndex != 5 {
		t.Errorf("max |t| at index %d, expected 5", result.MaxAbsTIndex)
	}

	// The t-value at the leakage point should be very large.
	if result.MaxAbsT < 4.5 {
		t.Errorf("max |t| = %f, expected > 4.5 for obvious leakage", result.MaxAbsT)
	}

	if !result.Leakage {
		t.Error("should detect leakage")
	}

	// Non-leaking samples should have much smaller |t| than leaking sample.
	for i, tv := range result.TValues {
		if i != 5 && math.Abs(tv) > 1.0 {
			t.Errorf("sample %d: |t| = %f, expected < 1.0 for non-leaking sample", i, math.Abs(tv))
		}
	}
}

func TestWelchTTestNoLeakage(t *testing.T) {
	numSamples := 10
	fixed := make([][]float64, 100)
	random := make([][]float64, 100)

	for i := range fixed {
		trace := make([]float64, numSamples)
		for j := range trace {
			trace[j] = 5.0
		}
		fixed[i] = trace
	}

	for i := range random {
		trace := make([]float64, numSamples)
		for j := range trace {
			trace[j] = 5.0
		}
		random[i] = trace
	}

	result, err := WelchTTest(fixed, random)
	if err != nil {
		t.Fatalf("WelchTTest: %v", err)
	}

	if result.Leakage {
		t.Error("should not detect leakage for identical traces")
	}
}
