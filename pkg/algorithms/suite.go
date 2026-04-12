package algorithms

import (
	"fmt"
	"strings"

	"github.com/akhilesharora/pqc-testkit/pkg/kat"
)

// Suite defines the interface that all PQC algorithm implementations must
// satisfy. It provides a uniform way to run KAT validation and benchmarks
// across different algorithm families (KEM and signature schemes).
type Suite interface {
	// Name returns the canonical algorithm name (e.g., "ML-KEM", "ML-DSA").
	Name() string

	// Type returns the algorithm type: "kem" or "sign".
	Type() string

	// Levels returns the supported security parameter sets.
	// For ML-KEM: [512, 768, 1024]. For ML-DSA: [44, 65, 87].
	Levels() []int

	// SupportsLevel reports whether the given security level is valid.
	SupportsLevel(level int) bool

	// KeyGen generates a key pair for the given security level.
	// Returns (publicKey, secretKey, error).
	KeyGen(level int) ([]byte, []byte, error)

	// RunKAT executes a single KAT vector against the implementation and
	// returns whether the output matches the expected values.
	RunKAT(level int, vec kat.Vector) (kat.Result, error)
}

// KEMSuite extends Suite with KEM-specific operations for key encapsulation
// mechanisms like ML-KEM.
type KEMSuite interface {
	Suite

	// Encapsulate generates a shared secret and ciphertext from a public key.
	// Returns (ciphertext, sharedSecret, error).
	Encapsulate(level int, publicKey []byte) ([]byte, []byte, error)

	// Decapsulate recovers the shared secret from a ciphertext and secret key.
	// Returns (sharedSecret, error).
	Decapsulate(level int, secretKey, ciphertext []byte) ([]byte, error)
}

// SignSuite extends Suite with signature-specific operations for digital
// signature schemes like ML-DSA and SLH-DSA.
type SignSuite interface {
	Suite

	// Sign produces a signature over a message using the secret key.
	Sign(level int, secretKey, message []byte) ([]byte, error)

	// Verify checks a signature against a message and public key.
	Verify(level int, publicKey, message, signature []byte) (bool, error)
}

// NewSuite creates a Suite for the given algorithm name. Supported names
// are "ml-kem", "ml-dsa", and "slh-dsa" (case-insensitive, with or without
// hyphens).
func NewSuite(name string) (Suite, error) {
	switch strings.ToLower(strings.ReplaceAll(name, "-", "")) {
	case "mlkem":
		return NewMLKEM(), nil
	case "mldsa":
		return NewMLDSA(), nil
	case "slhdsa":
		return NewSLHDSA(), nil
	default:
		return nil, fmt.Errorf("unknown algorithm: %s (supported: ml-kem, ml-dsa, slh-dsa)", name)
	}
}
