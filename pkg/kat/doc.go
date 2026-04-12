// Package kat provides parsing and validation of NIST Known Answer Test (KAT)
// vectors for post-quantum cryptography algorithms.
//
// KAT vectors are the authoritative test data published by NIST for validating
// PQC implementations. Each vector contains deterministic inputs (seeds, messages)
// and expected outputs (keys, ciphertexts, signatures) that any correct
// implementation must reproduce exactly.
//
// Supported formats:
//   - NIST .rsp (response) files for ML-KEM, ML-DSA, and SLH-DSA
//   - JSON vector files for custom test cases
//
// Usage:
//
//	vectors, err := kat.LoadVectors("vectors/", "ml-kem", 768)
//	for _, v := range vectors {
//	    // v.Seed, v.Expected, etc.
//	}
package kat
