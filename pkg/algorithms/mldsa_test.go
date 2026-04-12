package algorithms

import (
	"testing"
)

func TestMLDSAName(t *testing.T) {
	m := NewMLDSA()
	if m.Name() != "ML-DSA" {
		t.Errorf("expected ML-DSA, got %s", m.Name())
	}
	if m.Type() != "sign" {
		t.Errorf("expected sign, got %s", m.Type())
	}
}

func TestMLDSALevels(t *testing.T) {
	m := NewMLDSA()
	levels := m.Levels()
	if len(levels) != 3 {
		t.Fatalf("expected 3 levels, got %d", len(levels))
	}
	for _, l := range []int{44, 65, 87} {
		if !m.SupportsLevel(l) {
			t.Errorf("should support level %d", l)
		}
	}
}

func TestMLDSAKeyGen(t *testing.T) {
	m := NewMLDSA()
	for _, level := range m.Levels() {
		t.Run(levelName("ML-DSA", level), func(t *testing.T) {
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

func TestMLDSASignVerify(t *testing.T) {
	m := NewMLDSA()
	msg := []byte("pqc-testkit ML-DSA sign/verify test message")

	for _, level := range m.Levels() {
		t.Run(levelName("ML-DSA", level), func(t *testing.T) {
			pk, sk, err := m.KeyGen(level)
			if err != nil {
				t.Fatalf("keygen: %v", err)
			}

			sig, err := m.Sign(level, sk, msg)
			if err != nil {
				t.Fatalf("sign: %v", err)
			}
			if len(sig) == 0 {
				t.Fatal("empty signature")
			}

			valid, err := m.Verify(level, pk, msg, sig)
			if err != nil {
				t.Fatalf("verify: %v", err)
			}
			if !valid {
				t.Error("valid signature rejected")
			}
		})
	}
}

func TestMLDSASignDeterministic(t *testing.T) {
	m := NewMLDSA()
	msg := []byte("deterministic signing test")

	pk, sk, err := m.KeyGen(44)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}

	sig1, err := m.Sign(44, sk, msg)
	if err != nil {
		t.Fatalf("sign 1: %v", err)
	}

	sig2, err := m.Sign(44, sk, msg)
	if err != nil {
		t.Fatalf("sign 2: %v", err)
	}

	if len(sig1) != len(sig2) {
		t.Fatalf("signature length mismatch: %d vs %d", len(sig1), len(sig2))
	}
	for i := range sig1 {
		if sig1[i] != sig2[i] {
			t.Fatal("deterministic signing produced different signatures")
		}
	}

	// Verify both signatures.
	valid, err := m.Verify(44, pk, msg, sig1)
	if err != nil || !valid {
		t.Error("sig1 verification failed")
	}
}

func TestMLDSAWrongKey(t *testing.T) {
	m := NewMLDSA()
	msg := []byte("wrong key test")

	_, sk, err := m.KeyGen(44)
	if err != nil {
		t.Fatalf("keygen 1: %v", err)
	}
	pk2, _, err := m.KeyGen(44)
	if err != nil {
		t.Fatalf("keygen 2: %v", err)
	}

	sig, err := m.Sign(44, sk, msg)
	if err != nil {
		t.Fatalf("sign: %v", err)
	}

	valid, err := m.Verify(44, pk2, msg, sig)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if valid {
		t.Error("signature verified with wrong public key")
	}
}
