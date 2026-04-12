package algorithms

import (
	"bytes"
	"fmt"

	"github.com/akhilesharora/pqc-testkit/pkg/kat"
	"github.com/cloudflare/circl/sign/mldsa/mldsa44"
	"github.com/cloudflare/circl/sign/mldsa/mldsa65"
	"github.com/cloudflare/circl/sign/mldsa/mldsa87"
)

// MLDSA implements the ML-DSA (FIPS 204) digital signature scheme,
// formerly known as CRYSTALS-Dilithium. It wraps cloudflare/circl's
// ML-DSA implementation.
type MLDSA struct{}

// NewMLDSA creates a new ML-DSA suite instance.
func NewMLDSA() *MLDSA {
	return &MLDSA{}
}

// Name returns "ML-DSA".
func (m *MLDSA) Name() string { return "ML-DSA" }

// Type returns "sign".
func (m *MLDSA) Type() string { return "sign" }

// Levels returns the supported ML-DSA parameter sets: 44, 65, 87.
func (m *MLDSA) Levels() []int { return []int{44, 65, 87} }

// SupportsLevel reports whether level is a valid ML-DSA parameter set.
func (m *MLDSA) SupportsLevel(level int) bool {
	return level == 44 || level == 65 || level == 87
}

// KeyGen generates an ML-DSA key pair at the specified security level.
// Returns the public key and private key as byte slices.
func (m *MLDSA) KeyGen(level int) ([]byte, []byte, error) {
	switch level {
	case 44:
		pk, sk, err := mldsa44.GenerateKey(nil)
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-44 keygen: %w", err)
		}
		pkBytes, err := pk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-44 marshal pk: %w", err)
		}
		skBytes, err := sk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-44 marshal sk: %w", err)
		}
		return pkBytes, skBytes, nil
	case 65:
		pk, sk, err := mldsa65.GenerateKey(nil)
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-65 keygen: %w", err)
		}
		pkBytes, err := pk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-65 marshal pk: %w", err)
		}
		skBytes, err := sk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-65 marshal sk: %w", err)
		}
		return pkBytes, skBytes, nil
	case 87:
		pk, sk, err := mldsa87.GenerateKey(nil)
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-87 keygen: %w", err)
		}
		pkBytes, err := pk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-87 marshal pk: %w", err)
		}
		skBytes, err := sk.MarshalBinary()
		if err != nil {
			return nil, nil, fmt.Errorf("ml-dsa-87 marshal sk: %w", err)
		}
		return pkBytes, skBytes, nil
	default:
		return nil, nil, fmt.Errorf("unsupported ml-dsa level: %d", level)
	}
}

// Sign produces a signature over a message using the secret key at the
// specified security level. Uses deterministic signing (randomized=false).
func (m *MLDSA) Sign(level int, secretKey, message []byte) ([]byte, error) {
	switch level {
	case 44:
		var sk mldsa44.PrivateKey
		if err := sk.UnmarshalBinary(secretKey); err != nil {
			return nil, fmt.Errorf("parsing sk-44: %w", err)
		}
		sig := make([]byte, mldsa44.SignatureSize)
		if err := mldsa44.SignTo((&sk), message, nil, false, sig); err != nil {
			return nil, fmt.Errorf("ml-dsa-44 sign: %w", err)
		}
		return sig, nil
	case 65:
		var sk mldsa65.PrivateKey
		if err := sk.UnmarshalBinary(secretKey); err != nil {
			return nil, fmt.Errorf("parsing sk-65: %w", err)
		}
		sig := make([]byte, mldsa65.SignatureSize)
		if err := mldsa65.SignTo((&sk), message, nil, false, sig); err != nil {
			return nil, fmt.Errorf("ml-dsa-65 sign: %w", err)
		}
		return sig, nil
	case 87:
		var sk mldsa87.PrivateKey
		if err := sk.UnmarshalBinary(secretKey); err != nil {
			return nil, fmt.Errorf("parsing sk-87: %w", err)
		}
		sig := make([]byte, mldsa87.SignatureSize)
		if err := mldsa87.SignTo((&sk), message, nil, false, sig); err != nil {
			return nil, fmt.Errorf("ml-dsa-87 sign: %w", err)
		}
		return sig, nil
	default:
		return nil, fmt.Errorf("unsupported ml-dsa level: %d", level)
	}
}

// Verify checks a signature against a message and public key at the
// specified security level.
func (m *MLDSA) Verify(level int, publicKey, message, signature []byte) (bool, error) {
	switch level {
	case 44:
		var pk mldsa44.PublicKey
		if err := pk.UnmarshalBinary(publicKey); err != nil {
			return false, fmt.Errorf("parsing pk-44: %w", err)
		}
		return mldsa44.Verify(&pk, message, nil, signature), nil
	case 65:
		var pk mldsa65.PublicKey
		if err := pk.UnmarshalBinary(publicKey); err != nil {
			return false, fmt.Errorf("parsing pk-65: %w", err)
		}
		return mldsa65.Verify(&pk, message, nil, signature), nil
	case 87:
		var pk mldsa87.PublicKey
		if err := pk.UnmarshalBinary(publicKey); err != nil {
			return false, fmt.Errorf("parsing pk-87: %w", err)
		}
		return mldsa87.Verify(&pk, message, nil, signature), nil
	default:
		return false, fmt.Errorf("unsupported ml-dsa level: %d", level)
	}
}

// RunKAT executes a KAT vector against the ML-DSA implementation. It tests
// signature verification: parses the public key and signature from the vector,
// and verifies the signature against the message.
func (m *MLDSA) RunKAT(level int, vec kat.Vector) (kat.Result, error) {
	if vec.PublicKey == nil || vec.SecretKey == nil || vec.Message == nil {
		return kat.Result{}, fmt.Errorf("incomplete KAT vector: missing pk, sk, or msg")
	}

	// If we have an expected signature, verify we can produce it.
	if vec.Signature != nil {
		sig, err := m.Sign(level, vec.SecretKey, vec.Message)
		if err != nil {
			return kat.Result{}, fmt.Errorf("sign failed: %w", err)
		}
		if !bytes.Equal(sig, vec.Signature) {
			return kat.Result{
				Match:         false,
				MismatchField: "signature",
				Got:           sig,
				Expected:      vec.Signature,
			}, nil
		}

		// Also verify the signature.
		valid, err := m.Verify(level, vec.PublicKey, vec.Message, sig)
		if err != nil {
			return kat.Result{}, fmt.Errorf("verify failed: %w", err)
		}
		if !valid {
			return kat.Result{
				Match:         false,
				MismatchField: "verification",
			}, nil
		}
	}

	return kat.Result{Match: true}, nil
}
