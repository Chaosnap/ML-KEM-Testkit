package algorithms

import (
	"fmt"

	"github.com/akhilesharora/pqc-testkit/pkg/kat"
	"github.com/cloudflare/circl/sign/slhdsa"
)

// SLHDSA implements the SLH-DSA (FIPS 205) hash-based digital signature
// scheme, formerly known as SPHINCS+. It wraps cloudflare/circl's SLH-DSA
// implementation.
type SLHDSA struct{}

// NewSLHDSA creates a new SLH-DSA suite instance.
func NewSLHDSA() *SLHDSA {
	return &SLHDSA{}
}

// Name returns "SLH-DSA".
func (s *SLHDSA) Name() string { return "SLH-DSA" }

// Type returns "sign".
func (s *SLHDSA) Type() string { return "sign" }

// Levels returns the supported SLH-DSA parameter sets, encoded as integers.
// The encoding is: security_level * 10 + speed (0=small, 1=fast).
//
//	128 = SHA2-128s, 129 = SHA2-128f
//	192 = SHA2-192s, 193 = SHA2-192f
//	256 = SHA2-256s, 257 = SHA2-256f
func (s *SLHDSA) Levels() []int {
	return []int{128, 129, 192, 193, 256, 257}
}

// SupportsLevel reports whether level is a valid SLH-DSA parameter set.
func (s *SLHDSA) SupportsLevel(level int) bool {
	for _, l := range s.Levels() {
		if l == level {
			return true
		}
	}
	return false
}

// paramSetID maps our integer level encoding to the circl slhdsa.ID.
func paramSetID(level int) (slhdsa.ID, error) {
	switch level {
	case 128:
		return slhdsa.SHA2_128s, nil
	case 129:
		return slhdsa.SHA2_128f, nil
	case 192:
		return slhdsa.SHA2_192s, nil
	case 193:
		return slhdsa.SHA2_192f, nil
	case 256:
		return slhdsa.SHA2_256s, nil
	case 257:
		return slhdsa.SHA2_256f, nil
	default:
		return 0, fmt.Errorf("unsupported slh-dsa level: %d", level)
	}
}

// KeyGen generates an SLH-DSA key pair at the specified parameter set.
// Returns the public key and private key as byte slices.
func (s *SLHDSA) KeyGen(level int) ([]byte, []byte, error) {
	id, err := paramSetID(level)
	if err != nil {
		return nil, nil, err
	}

	pk, sk, err := slhdsa.GenerateKey(nil, id)
	if err != nil {
		return nil, nil, fmt.Errorf("slh-dsa keygen: %w", err)
	}

	pkBytes, err := pk.MarshalBinary()
	if err != nil {
		return nil, nil, fmt.Errorf("slh-dsa marshal pk: %w", err)
	}
	skBytes, err := sk.MarshalBinary()
	if err != nil {
		return nil, nil, fmt.Errorf("slh-dsa marshal sk: %w", err)
	}
	return pkBytes, skBytes, nil
}

// Sign produces an SLH-DSA signature over a message using the secret key.
// Uses deterministic signing for reproducibility.
func (s *SLHDSA) Sign(level int, secretKey, message []byte) ([]byte, error) {
	id, err := paramSetID(level)
	if err != nil {
		return nil, err
	}

	sk := slhdsa.PrivateKey{ID: id}
	if err := sk.UnmarshalBinary(secretKey); err != nil {
		return nil, fmt.Errorf("parsing sk: %w", err)
	}

	msg := slhdsa.NewMessage(message)
	sig, err := slhdsa.SignDeterministic(&sk, msg, nil)
	if err != nil {
		return nil, fmt.Errorf("slh-dsa sign: %w", err)
	}
	return sig, nil
}

// Verify checks an SLH-DSA signature against a message and public key.
func (s *SLHDSA) Verify(level int, publicKey, message, signature []byte) (bool, error) {
	id, err := paramSetID(level)
	if err != nil {
		return false, err
	}

	pk := slhdsa.PublicKey{ID: id}
	if err := pk.UnmarshalBinary(publicKey); err != nil {
		return false, fmt.Errorf("parsing pk: %w", err)
	}

	msg := slhdsa.NewMessage(message)
	return slhdsa.Verify(&pk, msg, signature, nil), nil
}

// RunKAT executes a KAT vector against the SLH-DSA implementation. It tests
// signature verification: verifies the provided signature against the message
// and public key from the vector.
func (s *SLHDSA) RunKAT(level int, vec kat.Vector) (kat.Result, error) {
	if vec.PublicKey == nil || vec.Message == nil || vec.Signature == nil {
		return kat.Result{}, fmt.Errorf("incomplete KAT vector: missing pk, msg, or sig")
	}

	valid, err := s.Verify(level, vec.PublicKey, vec.Message, vec.Signature)
	if err != nil {
		return kat.Result{}, fmt.Errorf("verify failed: %w", err)
	}

	if !valid {
		return kat.Result{
			Match:         false,
			MismatchField: "verification",
		}, nil
	}

	return kat.Result{Match: true}, nil
}
