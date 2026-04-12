// Package algorithms provides a unified interface over post-quantum
// cryptography algorithm implementations.
//
// It wraps the concrete implementations from Go's standard library
// (crypto/mlkem) and cloudflare/circl (ML-DSA, SLH-DSA) behind a common
// [Suite] interface that supports keygen, encapsulation/signing, and
// decapsulation/verification with KAT vector validation.
//
// Supported algorithms:
//   - ML-KEM (FIPS 203): Key encapsulation mechanism, security levels 512/768/1024
//   - ML-DSA (FIPS 204): Digital signatures, security levels 44/65/87
//   - SLH-DSA (FIPS 205): Hash-based digital signatures, multiple parameter sets
package algorithms
