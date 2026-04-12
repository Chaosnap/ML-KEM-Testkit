package algorithms

import (
	"testing"
)

func TestSLHDSAName(t *testing.T) {
	s := NewSLHDSA()
	if s.Name() != "SLH-DSA" {
		t.Errorf("expected SLH-DSA, got %s", s.Name())
	}
	if s.Type() != "sign" {
		t.Errorf("expected sign, got %s", s.Type())
	}
}

func TestSLHDSALevels(t *testing.T) {
	s := NewSLHDSA()
	levels := s.Levels()
	if len(levels) != 6 {
		t.Fatalf("expected 6 levels, got %d", len(levels))
	}
	for _, l := range []int{128, 129, 192, 193, 256, 257} {
		if !s.SupportsLevel(l) {
			t.Errorf("should support level %d", l)
		}
	}
}

func TestSLHDSAKeyGen(t *testing.T) {
	s := NewSLHDSA()
	// Only test fast variants to keep test time reasonable.
	for _, level := range []int{129, 193, 257} {
		t.Run(levelName("SLH-DSA", level), func(t *testing.T) {
			pk, sk, err := s.KeyGen(level)
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

func TestSLHDSASignVerify(t *testing.T) {
	s := NewSLHDSA()
	msg := []byte("pqc-testkit SLH-DSA sign/verify test")

	// Test only SHA2-128f (level 129) to keep test time under control.
	// SLH-DSA signing is slow, especially for small (s) parameter sets.
	level := 129
	t.Run(levelName("SLH-DSA", level), func(t *testing.T) {
		pk, sk, err := s.KeyGen(level)
		if err != nil {
			t.Fatalf("keygen: %v", err)
		}

		sig, err := s.Sign(level, sk, msg)
		if err != nil {
			t.Fatalf("sign: %v", err)
		}
		if len(sig) == 0 {
			t.Fatal("empty signature")
		}

		valid, err := s.Verify(level, pk, msg, sig)
		if err != nil {
			t.Fatalf("verify: %v", err)
		}
		if !valid {
			t.Error("valid signature rejected")
		}
	})
}

func TestSLHDSAWrongMessage(t *testing.T) {
	s := NewSLHDSA()
	level := 129

	pk, sk, err := s.KeyGen(level)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}

	sig, err := s.Sign(level, sk, []byte("correct message"))
	if err != nil {
		t.Fatalf("sign: %v", err)
	}

	valid, err := s.Verify(level, pk, []byte("wrong message"), sig)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if valid {
		t.Error("signature verified with wrong message")
	}
}
