package kat

// Vector represents a single KAT test vector containing the inputs and
// expected outputs for one test case. Fields are populated based on the
// algorithm type (KEM vs signature).
type Vector struct {
	// Count is the zero-based index of this vector within its file.
	Count int

	// Seed is the deterministic seed (d || z for ML-KEM, xi for ML-DSA)
	// used to generate keys and random values.
	Seed []byte

	// PublicKey is the expected public key output from keygen.
	PublicKey []byte

	// SecretKey is the expected secret key output from keygen.
	SecretKey []byte

	// Ciphertext is the expected ciphertext from encapsulation (KEM only).
	Ciphertext []byte

	// SharedSecret is the expected shared secret from encaps/decaps (KEM only).
	SharedSecret []byte

	// Message is the message to be signed (signature algorithms only).
	Message []byte

	// Signature is the expected signature output (signature algorithms only).
	Signature []byte
}

// Result holds the outcome of running a single KAT vector against an
// implementation.
type Result struct {
	// Match is true if all outputs matched expected values.
	Match bool

	// MismatchField identifies which output field did not match,
	// empty if Match is true.
	MismatchField string

	// Got contains the actual output bytes for the mismatched field.
	Got []byte

	// Expected contains the expected output bytes for the mismatched field.
	Expected []byte
}
