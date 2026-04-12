package kat

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseRSP(t *testing.T) {
	// Create a temporary .rsp file with known content.
	dir := t.TempDir()
	rspContent := `# ML-KEM-768 KAT vectors

count = 0
seed = 7c9935a0b07694aa0c6d10e4db6b1add2fd81a25ccb14803
pk = aabbccdd
sk = 11223344
ct = deadbeef
ss = cafebabe

count = 1
seed = a0b07694aa0c6d10e4db6b1add2fd81a25ccb148037c9935
pk = 11111111
sk = 22222222
ct = 33333333
ss = 44444444
`
	rspPath := filepath.Join(dir, "test.rsp")
	if err := os.WriteFile(rspPath, []byte(rspContent), 0644); err != nil {
		t.Fatalf("writing test rsp: %v", err)
	}

	vectors, err := parseRSP(rspPath)
	if err != nil {
		t.Fatalf("parseRSP: %v", err)
	}

	if len(vectors) != 2 {
		t.Fatalf("expected 2 vectors, got %d", len(vectors))
	}

	// Check first vector.
	v := vectors[0]
	if v.Count != 0 {
		t.Errorf("vector 0 count: got %d, want 0", v.Count)
	}
	if len(v.PublicKey) != 4 {
		t.Errorf("vector 0 pk length: got %d, want 4", len(v.PublicKey))
	}
	if v.PublicKey[0] != 0xaa || v.PublicKey[1] != 0xbb {
		t.Errorf("vector 0 pk: got %x, want aabbccdd", v.PublicKey)
	}
	if len(v.SharedSecret) != 4 {
		t.Errorf("vector 0 ss length: got %d, want 4", len(v.SharedSecret))
	}

	// Check second vector.
	v = vectors[1]
	if v.Count != 1 {
		t.Errorf("vector 1 count: got %d, want 1", v.Count)
	}
}

func TestParseRSPWithAlternativeFieldNames(t *testing.T) {
	dir := t.TempDir()
	rspContent := `count = 0
ek = aabbccdd
dk = 11223344
ct = deadbeef
ss = cafebabe
`
	rspPath := filepath.Join(dir, "alt.rsp")
	if err := os.WriteFile(rspPath, []byte(rspContent), 0644); err != nil {
		t.Fatalf("writing test rsp: %v", err)
	}

	vectors, err := parseRSP(rspPath)
	if err != nil {
		t.Fatalf("parseRSP: %v", err)
	}

	if len(vectors) != 1 {
		t.Fatalf("expected 1 vector, got %d", len(vectors))
	}

	// ek should map to PublicKey, dk to SecretKey.
	if vectors[0].PublicKey == nil {
		t.Error("ek not mapped to PublicKey")
	}
	if vectors[0].SecretKey == nil {
		t.Error("dk not mapped to SecretKey")
	}
}

func TestParseRSPSignatureVector(t *testing.T) {
	dir := t.TempDir()
	rspContent := `count = 0
pk = aabb
sk = ccdd
msg = 48656c6c6f
sig = eeff0011
`
	rspPath := filepath.Join(dir, "sig.rsp")
	if err := os.WriteFile(rspPath, []byte(rspContent), 0644); err != nil {
		t.Fatalf("writing test rsp: %v", err)
	}

	vectors, err := parseRSP(rspPath)
	if err != nil {
		t.Fatalf("parseRSP: %v", err)
	}

	if len(vectors) != 1 {
		t.Fatalf("expected 1 vector, got %d", len(vectors))
	}

	v := vectors[0]
	if string(v.Message) != "Hello" {
		t.Errorf("message: got %q, want %q", v.Message, "Hello")
	}
	if v.Signature == nil {
		t.Error("signature not parsed")
	}
}

func TestLoadVectorsMissing(t *testing.T) {
	_, err := LoadVectors("/nonexistent", "ml-kem", 768)
	if err == nil {
		t.Error("expected error for missing vectors directory")
	}
}

func TestVectorFilename(t *testing.T) {
	tests := []struct {
		algo  string
		level int
		want  string
	}{
		{"ml-kem", 768, "ML-KEM-768.rsp"},
		{"mlkem", 1024, "ML-KEM-1024.rsp"},
		{"ml-dsa", 44, "ML-DSA-44.rsp"},
		{"slh-dsa", 128, "SLH-DSA-128.rsp"},
	}

	for _, tt := range tests {
		got := vectorFilename(tt.algo, tt.level)
		if got != tt.want {
			t.Errorf("vectorFilename(%q, %d) = %q, want %q", tt.algo, tt.level, got, tt.want)
		}
	}
}
