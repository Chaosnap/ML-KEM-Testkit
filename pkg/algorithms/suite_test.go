package algorithms

import (
	"testing"
)

func TestNewSuite(t *testing.T) {
	tests := []struct {
		name     string
		wantName string
		wantType string
	}{
		{"ml-kem", "ML-KEM", "kem"},
		{"ML-KEM", "ML-KEM", "kem"},
		{"mlkem", "ML-KEM", "kem"},
		{"ml-dsa", "ML-DSA", "sign"},
		{"ML-DSA", "ML-DSA", "sign"},
		{"mldsa", "ML-DSA", "sign"},
		{"slh-dsa", "SLH-DSA", "sign"},
		{"SLH-DSA", "SLH-DSA", "sign"},
		{"slhdsa", "SLH-DSA", "sign"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s, err := NewSuite(tt.name)
			if err != nil {
				t.Fatalf("NewSuite(%q): %v", tt.name, err)
			}
			if s.Name() != tt.wantName {
				t.Errorf("Name() = %s, want %s", s.Name(), tt.wantName)
			}
			if s.Type() != tt.wantType {
				t.Errorf("Type() = %s, want %s", s.Type(), tt.wantType)
			}
		})
	}
}

func TestNewSuiteInvalid(t *testing.T) {
	_, err := NewSuite("rsa")
	if err == nil {
		t.Error("expected error for unsupported algorithm")
	}
}

func TestSuiteInterfaces(t *testing.T) {
	kem, _ := NewSuite("ml-kem")
	if _, ok := kem.(KEMSuite); !ok {
		t.Error("ML-KEM should implement KEMSuite")
	}

	dsa, _ := NewSuite("ml-dsa")
	if _, ok := dsa.(SignSuite); !ok {
		t.Error("ML-DSA should implement SignSuite")
	}

	slh, _ := NewSuite("slh-dsa")
	if _, ok := slh.(SignSuite); !ok {
		t.Error("SLH-DSA should implement SignSuite")
	}
}
