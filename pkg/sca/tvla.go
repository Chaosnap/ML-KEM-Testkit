package sca

import (
	"crypto/rand"
	"fmt"
	"math"
)

// TVLAConfig controls the generation of TVLA test vector sets.
type TVLAConfig struct {
	// FixedKey is the constant key used for the "fixed" class.
	// If nil, a random key is generated once and reused.
	FixedKey []byte

	// FixedMessage is the constant message for fixed-vs-random message TVLA.
	// If nil, a random message is generated once and reused.
	FixedMessage []byte

	// NumTraces is the total number of traces to generate (split equally
	// between fixed and random classes).
	NumTraces int

	// KeySize is the key size in bytes for the target algorithm.
	KeySize int

	// MessageSize is the message/input size in bytes.
	MessageSize int
}

// TVLAVector represents a single TVLA test vector with its class label.
type TVLAVector struct {
	// Class is 0 for fixed, 1 for random.
	Class int

	// Key is the key bytes for this trace.
	Key []byte

	// Input is the message/plaintext bytes for this trace.
	Input []byte
}

// TVLAResult holds the result of a Welch's t-test evaluation over
// collected power traces.
type TVLAResult struct {
	// TValues contains the t-statistic at each sample point.
	TValues []float64

	// MaxAbsT is the maximum |t| value across all sample points.
	MaxAbsT float64

	// MaxAbsTIndex is the sample index where MaxAbsT occurs.
	MaxAbsTIndex int

	// Leakage is true if MaxAbsT exceeds the threshold (typically 4.5).
	Leakage bool

	// NumFixed is the number of fixed-class traces used.
	NumFixed int

	// NumRandom is the number of random-class traces used.
	NumRandom int
}

// GenerateTVLA creates a set of TVLA test vectors following the
// fixed-vs-random methodology. Vectors alternate between fixed (class 0)
// and random (class 1) inputs to enable non-specific leakage detection.
func GenerateTVLA(cfg TVLAConfig) ([]TVLAVector, error) {
	if cfg.NumTraces < 2 {
		return nil, fmt.Errorf("need at least 2 traces, got %d", cfg.NumTraces)
	}
	if cfg.KeySize <= 0 || cfg.MessageSize <= 0 {
		return nil, fmt.Errorf("key and message sizes must be positive")
	}

	// Generate or use provided fixed values.
	fixedKey := cfg.FixedKey
	if fixedKey == nil {
		fixedKey = make([]byte, cfg.KeySize)
		if _, err := rand.Read(fixedKey); err != nil {
			return nil, fmt.Errorf("generating fixed key: %w", err)
		}
	}

	fixedMsg := cfg.FixedMessage
	if fixedMsg == nil {
		fixedMsg = make([]byte, cfg.MessageSize)
		if _, err := rand.Read(fixedMsg); err != nil {
			return nil, fmt.Errorf("generating fixed message: %w", err)
		}
	}

	vectors := make([]TVLAVector, cfg.NumTraces)
	for i := range vectors {
		if i%2 == 0 {
			// Fixed class.
			vectors[i] = TVLAVector{
				Class: 0,
				Key:   append([]byte(nil), fixedKey...),
				Input: append([]byte(nil), fixedMsg...),
			}
		} else {
			// Random class.
			key := make([]byte, cfg.KeySize)
			msg := make([]byte, cfg.MessageSize)
			if _, err := rand.Read(key); err != nil {
				return nil, fmt.Errorf("generating random key: %w", err)
			}
			if _, err := rand.Read(msg); err != nil {
				return nil, fmt.Errorf("generating random message: %w", err)
			}
			vectors[i] = TVLAVector{
				Class: 1,
				Key:   key,
				Input: msg,
			}
		}
	}

	return vectors, nil
}

// WelchTTest computes Welch's t-test between two sets of power traces
// (fixed and random classes). Each trace is a slice of float64 sample values.
// Returns the t-statistic at each sample point.
//
// The threshold for detecting leakage is typically |t| > 4.5, which
// corresponds to a confidence level > 99.999%.
func WelchTTest(fixed, random [][]float64) (TVLAResult, error) {
	if len(fixed) == 0 || len(random) == 0 {
		return TVLAResult{}, fmt.Errorf("need at least one trace in each class")
	}

	numSamples := len(fixed[0])
	for _, t := range append(fixed, random...) {
		if len(t) != numSamples {
			return TVLAResult{}, fmt.Errorf("all traces must have the same number of samples")
		}
	}

	nf := float64(len(fixed))
	nr := float64(len(random))
	tValues := make([]float64, numSamples)

	for s := range numSamples {
		// Compute means.
		var sumF, sumR float64
		for _, t := range fixed {
			sumF += t[s]
		}
		for _, t := range random {
			sumR += t[s]
		}
		meanF := sumF / nf
		meanR := sumR / nr

		// Compute variances.
		var varF, varR float64
		for _, t := range fixed {
			d := t[s] - meanF
			varF += d * d
		}
		for _, t := range random {
			d := t[s] - meanR
			varR += d * d
		}
		varF /= nf - 1
		varR /= nr - 1

		// Welch's t-statistic.
		denom := math.Sqrt(varF/nf + varR/nr)
		if denom > 0 {
			tValues[s] = (meanF - meanR) / denom
		}
	}

	// Find maximum |t|.
	result := TVLAResult{
		TValues:   tValues,
		NumFixed:  len(fixed),
		NumRandom: len(random),
	}
	for i, t := range tValues {
		absT := math.Abs(t)
		if absT > result.MaxAbsT {
			result.MaxAbsT = absT
			result.MaxAbsTIndex = i
		}
	}
	result.Leakage = result.MaxAbsT > 4.5

	return result, nil
}
