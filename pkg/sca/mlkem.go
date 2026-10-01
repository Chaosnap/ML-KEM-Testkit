package sca

import (
	"crypto/rand"
	"crypto/sha3"
	"fmt"
	"math/big"

	"github.com/cloudflare/circl/kem/mlkem/mlkem768"
)

// MLKEMOp selects the ML-KEM operation targeted by a TVLA campaign.
type MLKEMOp int

const (
	// MLKEMKeyGen varies the KeyGen seed d||z: fixed seed vs random seeds.
	MLKEMKeyGen MLKEMOp = iota

	// MLKEMDecaps keeps the decapsulation key fixed and varies the
	// ciphertext: one fixed ciphertext vs a fresh ciphertext per trace.
	MLKEMDecaps
)

// String returns the operation name used on the command line.
func (op MLKEMOp) String() string {
	switch op {
	case MLKEMKeyGen:
		return "keygen"
	case MLKEMDecaps:
		return "decaps"
	default:
		return fmt.Sprintf("MLKEMOp(%d)", int(op))
	}
}

// ParseMLKEMOp converts "keygen" or "decaps" to an MLKEMOp.
func ParseMLKEMOp(s string) (MLKEMOp, error) {
	switch s {
	case "keygen":
		return MLKEMKeyGen, nil
	case "decaps":
		return MLKEMDecaps, nil
	default:
		return 0, fmt.Errorf("unknown ML-KEM TVLA operation %q (want keygen or decaps)", s)
	}
}

// MLKEM768 sizes in bytes (FIPS 203, k = 3).
const (
	MLKEM768SeedSize       = 64   // KeyGen input d||z.
	MLKEM768DecapsKeySize  = 2400 // dk.
	MLKEM768CiphertextSize = 1088 // c.
)

// MLKEMTVLAConfig controls ML-KEM-768 fixed-vs-random vector generation.
type MLKEMTVLAConfig struct {
	// Op is the targeted operation.
	Op MLKEMOp

	// NumTraces is the total number of vectors, split equally between the
	// fixed and random classes. Must be even and at least 4.
	NumTraces int

	// FixedSeed is the KeyGen seed d||z of the fixed class (MLKEMKeyGen),
	// or the seed of the fixed key pair (MLKEMDecaps). The fixed class is
	// a deterministic function of it; the random class never is. Random if
	// nil.
	FixedSeed []byte

	// InvalidRandom makes the random class of a MLKEMDecaps campaign use
	// uniformly random ciphertexts instead of valid encapsulations. Those
	// fail the FO re-encryption check, so the test then compares the
	// accept path (fixed class) with the implicit-rejection path.
	InvalidRandom bool
}

// MLKEMVector is one ML-KEM TVLA input with its class label.
type MLKEMVector struct {
	// Class is 0 for fixed, 1 for random.
	Class int

	// Input is the exact byte string for the core's data buffer:
	// d||z for KeyGen, dk||c for Decaps.
	Input []byte
}

// GenerateMLKEMTVLA creates ML-KEM-768 fixed-vs-random vectors in the
// input format of the hardware core. Exactly half of the vectors belong to
// each class and their order is randomly shuffled, so that slow drifts in
// the measurement setup do not correlate with the class label.
func GenerateMLKEMTVLA(cfg MLKEMTVLAConfig) ([]MLKEMVector, error) {
	if cfg.NumTraces < 4 || cfg.NumTraces%2 != 0 {
		return nil, fmt.Errorf("need an even number of at least 4 traces, got %d", cfg.NumTraces)
	}
	seed := cfg.FixedSeed
	if seed == nil {
		seed = make([]byte, MLKEM768SeedSize)
		if _, err := rand.Read(seed); err != nil {
			return nil, err
		}
	}
	if len(seed) != MLKEM768SeedSize {
		return nil, fmt.Errorf("fixed seed must be %d bytes, got %d", MLKEM768SeedSize, len(seed))
	}

	var fixed []byte
	var random func() ([]byte, error)
	switch cfg.Op {
	case MLKEMKeyGen:
		fixed = seed
		random = func() ([]byte, error) { return randomBytes(MLKEM768SeedSize) }
	case MLKEMDecaps:
		pk, sk := mlkem768.NewKeyFromSeed(seed)
		dk, err := sk.MarshalBinary()
		if err != nil {
			return nil, err
		}
		encapsTo := func(m []byte) []byte {
			ct := make([]byte, MLKEM768CiphertextSize)
			ss := make([]byte, mlkem768.SharedKeySize)
			pk.EncapsulateTo(ct, ss, m)
			return ct
		}
		encaps := func() ([]byte, error) {
			m, err := randomBytes(32)
			if err != nil {
				return nil, err
			}
			return encapsTo(m), nil
		}
		// The fixed message is derived from the seed, so the whole fixed
		// class (dk and c) is reproducible from FixedSeed.
		m0 := sha3.Sum256(append([]byte("pqc-testkit tvla fixed m"), seed...))
		ct0 := encapsTo(m0[:])
		fixed = append(append([]byte(nil), dk...), ct0...)
		random = func() ([]byte, error) {
			var ct []byte
			var err error
			if cfg.InvalidRandom {
				ct, err = randomBytes(MLKEM768CiphertextSize)
			} else {
				ct, err = encaps()
			}
			if err != nil {
				return nil, err
			}
			return append(append([]byte(nil), dk...), ct...), nil
		}
	default:
		return nil, fmt.Errorf("unsupported operation %v", cfg.Op)
	}

	vectors := make([]MLKEMVector, cfg.NumTraces)
	for i := range vectors {
		if i < cfg.NumTraces/2 {
			vectors[i] = MLKEMVector{Class: 0, Input: append([]byte(nil), fixed...)}
			continue
		}
		in, err := random()
		if err != nil {
			return nil, err
		}
		vectors[i] = MLKEMVector{Class: 1, Input: in}
	}
	if err := shuffle(vectors); err != nil {
		return nil, err
	}
	return vectors, nil
}

// randomBytes returns n bytes from crypto/rand.
func randomBytes(n int) ([]byte, error) {
	b := make([]byte, n)
	_, err := rand.Read(b)
	return b, err
}

// shuffle permutes v uniformly (Fisher-Yates with crypto/rand).
func shuffle(v []MLKEMVector) error {
	for i := len(v) - 1; i > 0; i-- {
		j, err := rand.Int(rand.Reader, big.NewInt(int64(i+1)))
		if err != nil {
			return err
		}
		k := int(j.Int64())
		v[i], v[k] = v[k], v[i]
	}
	return nil
}
