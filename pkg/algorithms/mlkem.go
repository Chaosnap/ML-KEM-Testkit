package algorithms

import (
	"bytes"
	"crypto/mlkem"
	"crypto/rand"
	"fmt"

	"github.com/akhilesharora/pqc-testkit/pkg/kat"
	"github.com/cloudflare/circl/kem/mlkem/mlkem512"
)

// MLKEM implements the ML-KEM (FIPS 203) key encapsulation mechanism,
// formerly known as CRYSTALS-Kyber. It wraps Go's standard library
// crypto/mlkem for levels 768/1024 and cloudflare/circl for level 512.
type MLKEM struct{}

// NewMLKEM creates a new ML-KEM suite instance.
func NewMLKEM() *MLKEM {
	return &MLKEM{}
}

// Name returns "ML-KEM".
func (m *MLKEM) Name() string { return "ML-KEM" }

// Type returns "kem".
func (m *MLKEM) Type() string { return "kem" }

// Levels returns the supported ML-KEM parameter sets: 512, 768, and 1024.
func (m *MLKEM) Levels() []int { return []int{512, 768, 1024} }

// SupportsLevel reports whether level is a valid ML-KEM parameter set.
func (m *MLKEM) SupportsLevel(level int) bool {
	return level == 512 || level == 768 || level == 1024
}

// KeyGen generates an ML-KEM key pair at the specified security level.
// Returns the encapsulation key and decapsulation key as byte slices.
func (m *MLKEM) KeyGen(level int) ([]byte, []byte, error) {
	switch level {
	case 512:
		pk, sk, err := mlkem512.GenerateKeyPair(rand.Reader)
		if err != nil {
			return nil, nil, fmt.Errorf("ml-kem-512 keygen: %w", err)
		}
		pkBytes, err := pk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-kem-512 marshal pk: %w", err)
		}
		skBytes, err := sk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-kem-512 marshal sk: %w", err)
		}
		return pkBytes, skBytes, nil
	case 768:
		dk, err := mlkem.GenerateKey768()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-kem-768 keygen: %w", err)
		}
		return dk.EncapsulationKey().Bytes(), dk.Bytes(), nil
	case 1024:
		dk, err := mlkem.GenerateKey1024()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-kem-1024 keygen: %w", err)
		}
		return dk.EncapsulationKey().Bytes(), dk.Bytes(), nil
	default:
		return nil, nil, fmt.Errorf("unsupported ml-kem level: %d", level)
	}
}

// Encapsulate generates a shared secret and ciphertext from a public
// (encapsulation) key at the specified security level.
func (m *MLKEM) Encapsulate(level int, publicKey []byte) ([]byte, []byte, error) {
	switch level {
	case 512:
		var pk mlkem512.PublicKey
		if err := pk.Unpack(publicKey); err != nil {
			return nil, nil, fmt.Errorf("parsing ek-512: %w", err)
		}
		scheme := mlkem512.Scheme()
		ct := make([]byte, scheme.CiphertextSize())
		ss := make([]byte, scheme.SharedKeySize())
		pk.EncapsulateTo(ct, ss, nil)
		return ct, ss, nil
	case 768:
		ek, err := mlkem.NewEncapsulationKey768(publicKey)
		if err != nil {
			return nil, nil, fmt.Errorf("parsing ek-768: %w", err)
		}
		ss, ct := ek.Encapsulate()
		return ct, ss, nil
	case 1024:
		ek, err := mlkem.NewEncapsulationKey1024(publicKey)
		if err != nil {
			return nil, nil, fmt.Errorf("parsing ek-1024: %w", err)
		}
		ss, ct := ek.Encapsulate()
		return ct, ss, nil
	default:
		return nil, nil, fmt.Errorf("unsupported ml-kem level: %d", level)
	}
}

// Decapsulate recovers the shared secret from a ciphertext and secret
// (decapsulation) key at the specified security level.
func (m *MLKEM) Decapsulate(level int, secretKey, ciphertext []byte) ([]byte, error) {
	switch level {
	case 512:
		var sk mlkem512.PrivateKey
		if err := sk.Unpack(secretKey); err != nil {
			return nil, fmt.Errorf("parsing dk-512: %w", err)
		}
		scheme := mlkem512.Scheme()
		ss := make([]byte, scheme.SharedKeySize())
		sk.DecapsulateTo(ss, ciphertext)
		return ss, nil
	case 768:
		dk, err := mlkem.NewDecapsulationKey768(secretKey)
		if err != nil {
			return nil, fmt.Errorf("parsing dk-768: %w", err)
		}
		ss, err := dk.Decapsulate(ciphertext)
		if err != nil {
			return nil, fmt.Errorf("decapsulate-768: %w", err)
		}
		return ss, nil
	case 1024:
		dk, err := mlkem.NewDecapsulationKey1024(secretKey)
		if err != nil {
			return nil, fmt.Errorf("parsing dk-1024: %w", err)
		}
		ss, err := dk.Decapsulate(ciphertext)
		if err != nil {
			return nil, fmt.Errorf("decapsulate-1024: %w", err)
		}
		return ss, nil
	default:
		return nil, fmt.Errorf("unsupported ml-kem level: %d", level)
	}
}

// RunKAT executes a KAT vector against the ML-KEM implementation. It tests
// the decapsulation path: parses the secret key and ciphertext from the vector,
// performs decapsulation, and compares the shared secret against the expected value.
func (m *MLKEM) RunKAT(level int, vec kat.Vector) (kat.Result, error) {
	if vec.SecretKey == nil || vec.Ciphertext == nil || vec.SharedSecret == nil {
		return kat.Result{}, fmt.Errorf("incomplete KAT vector: missing sk, ct, or ss")
	}

	ss, err := m.Decapsulate(level, vec.SecretKey, vec.Ciphertext)
	if err != nil {
		return kat.Result{}, fmt.Errorf("decapsulate failed: %w", err)
	}

	if !bytes.Equal(ss, vec.SharedSecret) {
		return kat.Result{
			Match:         false,
			MismatchField: "shared_secret",
			Got:           ss,
			Expected:      vec.SharedSecret,
		}, nil
	}

	return kat.Result{Match: true}, nil
}
