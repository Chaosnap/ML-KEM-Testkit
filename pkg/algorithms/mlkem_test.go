package algorithms

import (
	"testing"
)

func TestMLKEMName(t *testing.T) {
	m := NewMLKEM()
	if m.Name() != "ML-KEM" {
		t.Errorf("expected ML-KEM, got %s", m.Name())
	}
	if m.Type() != "kem" {
		t.Errorf("expected kem, got %s", m.Type())
	}
}

func TestMLKEMLevels(t *testing.T) {
	m := NewMLKEM()
	levels := m.Levels()
	if len(levels) != 3 {
		t.Fatalf("expected 3 levels, got %d", len(levels))
	}
	for _, l := range []int{512, 768, 1024} {
		if !m.SupportsLevel(l) {
			t.Errorf("should support level %d", l)
		}
	}
}

func TestMLKEMKeyGen(t *testing.T) {
	m := NewMLKEM()
	for _, level := range m.Levels() {
		t.Run(levelName("ML-KEM", level), func(t *testing.T) {
			pk, sk, err := m.KeyGen(level)
			if err != nil {
				t.Fatalf("keygen failed: %v", err)
			}
			if len(pk) == 0 {
				t.Error("empty public key")
			}
			if len(sk) == 0 {
				t.Error("empty secret key")
			}
		})
	}
}

func TestMLKEMEncapsDecaps(t *testing.T) {
	m := NewMLKEM()
	for _, level := range m.Levels() {
		t.Run(levelName("ML-KEM", level), func(t *testing.T) {
			pk, sk, err := m.KeyGen(level)
			if err != nil {
				t.Fatalf("keygen: %v", err)
			}

			ct, ss1, err := m.Encapsulate(level, pk)
			if err != nil {
				t.Fatalf("encapsulate: %v", err)
			}
			if len(ct) == 0 || len(ss1) == 0 {
				t.Fatal("empty ciphertext or shared secret")
			}

			ss2, err := m.Decapsulate(level, sk, ct)
			if err != nil {
				t.Fatalf("decapsulate: %v", err)
			}

			if len(ss1) != len(ss2) {
				t.Fatalf("shared secret length mismatch: %d vs %d", len(ss1), len(ss2))
			}
			for i := range ss1 {
				if ss1[i] != ss2[i] {
					t.Fatalf("shared secret mismatch at byte %d", i)
				}
			}
		})
	}
}

func TestMLKEMInvalidLevel(t *testing.T) {
	m := NewMLKEM()
	_, _, err := m.KeyGen(256)
	if err == nil {
		t.Error("expected error for unsupported level 256")
	}
}

func levelName(algo string, level int) string {
	return algo + "-" + itoa(level)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	s := ""
	for n > 0 {
		s = string(rune('0'+n%10)) + s
		n /= 10
	}
	return s
}
