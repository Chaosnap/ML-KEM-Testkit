package kat

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// LoadVectors reads NIST KAT vectors from the given directory for the
// specified algorithm and security level. It looks for .rsp files following
// NIST naming conventions.
//
// For ML-KEM, level is the parameter set (512, 768, 1024).
// For ML-DSA, level is the parameter set (44, 65, 87).
// For SLH-DSA, level follows the NIST naming (128s, 128f, 192s, etc.).
func LoadVectors(dir, algorithm string, level int) ([]Vector, error) {
	pattern := vectorFilename(algorithm, level)
	path := filepath.Join(dir, algorithm, pattern)

	// Try exact path first, then glob for variants.
	if _, err := os.Stat(path); os.IsNotExist(err) {
		matches, globErr := filepath.Glob(filepath.Join(dir, algorithm, fmt.Sprintf("*%d*", level)))
		if globErr != nil || len(matches) == 0 {
			return nil, fmt.Errorf("no KAT vectors found for %s level %d in %s", algorithm, level, dir)
		}
		path = matches[0]
	}

	return parseRSP(path)
}

// vectorFilename returns the expected KAT vector filename for an algorithm
// and security level.
func vectorFilename(algorithm string, level int) string {
	switch strings.ToLower(algorithm) {
	case "ml-kem", "mlkem":
		return fmt.Sprintf("ML-KEM-%d.rsp", level)
	case "ml-dsa", "mldsa":
		return fmt.Sprintf("ML-DSA-%d.rsp", level)
	case "slh-dsa", "slhdsa":
		return fmt.Sprintf("SLH-DSA-%d.rsp", level)
	default:
		return fmt.Sprintf("%s-%d.rsp", algorithm, level)
	}
}

// parseRSP parses a NIST .rsp (response) file into a slice of test vectors.
// The .rsp format consists of key=value pairs separated by blank lines,
// with each group representing one test vector.
func parseRSP(path string) ([]Vector, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("opening KAT file: %w", err)
	}
	defer f.Close()

	var vectors []Vector
	current := make(map[string]string)
	scanner := bufio.NewScanner(f)

	// Increase scanner buffer for large signature vectors.
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		// Skip comments and empty processing.
		if strings.HasPrefix(line, "#") {
			continue
		}

		if line == "" {
			if len(current) > 0 {
				v, err := mapToVector(current)
				if err != nil {
					return nil, fmt.Errorf("parsing vector at count %d: %w", len(vectors), err)
				}
				vectors = append(vectors, v)
				current = make(map[string]string)
			}
			continue
		}

		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			val := strings.TrimSpace(parts[1])
			current[key] = val
		}
	}

	// Handle last vector if file doesn't end with blank line.
	if len(current) > 0 {
		v, err := mapToVector(current)
		if err != nil {
			return nil, fmt.Errorf("parsing final vector: %w", err)
		}
		vectors = append(vectors, v)
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("reading KAT file: %w", err)
	}

	return vectors, nil
}

// mapToVector converts a key-value map parsed from an .rsp file into a
// typed Vector struct.
func mapToVector(m map[string]string) (Vector, error) {
	var v Vector
	var err error

	if s, ok := m["count"]; ok {
		v.Count, err = strconv.Atoi(s)
		if err != nil {
			return v, fmt.Errorf("parsing count: %w", err)
		}
	}

	v.Seed, _ = decodeHexField(m, "seed")
	v.PublicKey, _ = decodeHexField(m, "pk")
	v.SecretKey, _ = decodeHexField(m, "sk")
	v.Ciphertext, _ = decodeHexField(m, "ct")
	v.SharedSecret, _ = decodeHexField(m, "ss")
	v.Message, _ = decodeHexField(m, "msg")
	v.Signature, _ = decodeHexField(m, "sig")

	// Alternative field names used in some NIST vector formats.
	if v.PublicKey == nil {
		v.PublicKey, _ = decodeHexField(m, "ek")
	}
	if v.SecretKey == nil {
		v.SecretKey, _ = decodeHexField(m, "dk")
	}

	return v, nil
}

// decodeHexField extracts and decodes a hex-encoded field from the map.
// Returns nil, nil if the key is not present.
func decodeHexField(m map[string]string, key string) ([]byte, error) {
	s, ok := m[key]
	if !ok {
		return nil, nil
	}
	b, err := hex.DecodeString(s)
	if err != nil {
		return nil, fmt.Errorf("decoding %s: %w", key, err)
	}
	return b, nil
}
