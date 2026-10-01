package sca

import (
	"bytes"
	"math"
	"math/rand/v2"
	"testing"

	"github.com/cloudflare/circl/kem/mlkem/mlkem768"
)

func TestGenerateMLKEMTVLAKeyGen(t *testing.T) {
	vectors, err := GenerateMLKEMTVLA(MLKEMTVLAConfig{Op: MLKEMKeyGen, NumTraces: 200})
	if err != nil {
		t.Fatal(err)
	}
	var fixed []byte
	counts := [2]int{}
	for _, v := range vectors {
		counts[v.Class]++
		if len(v.Input) != MLKEM768SeedSize {
			t.Fatalf("input size %d, want %d", len(v.Input), MLKEM768SeedSize)
		}
		if v.Class == 0 {
			if fixed == nil {
				fixed = v.Input
			} else if !bytes.Equal(v.Input, fixed) {
				t.Fatal("fixed-class seeds differ")
			}
		} else if bytes.Equal(v.Input, fixed) {
			t.Fatal("random-class seed equals the fixed seed")
		}
	}
	if counts != [2]int{100, 100} {
		t.Fatalf("class split %v, want 100/100", counts)
	}
	// An unshuffled list would put all fixed vectors first.
	if firstHalfFixed := countClass(vectors[:100], 0); firstHalfFixed == 100 {
		t.Fatal("vectors are not shuffled")
	}
}

func TestGenerateMLKEMTVLADecaps(t *testing.T) {
	seed := bytes.Repeat([]byte{7}, MLKEM768SeedSize)
	for _, invalid := range []bool{false, true} {
		vectors, err := GenerateMLKEMTVLA(MLKEMTVLAConfig{
			Op: MLKEMDecaps, NumTraces: 20, FixedSeed: seed, InvalidRandom: invalid,
		})
		if err != nil {
			t.Fatal(err)
		}
		_, sk := mlkem768.NewKeyFromSeed(seed)
		dk, _ := sk.MarshalBinary()
		var fixedCT []byte
		for _, v := range vectors {
			if len(v.Input) != MLKEM768DecapsKeySize+MLKEM768CiphertextSize {
				t.Fatalf("input size %d", len(v.Input))
			}
			if !bytes.Equal(v.Input[:MLKEM768DecapsKeySize], dk) {
				t.Fatal("dk is not the fixed key")
			}
			ct := v.Input[MLKEM768DecapsKeySize:]
			if v.Class == 0 {
				if fixedCT == nil {
					fixedCT = ct
				} else if !bytes.Equal(ct, fixedCT) {
					t.Fatal("fixed-class ciphertexts differ")
				}
			}
		}
		if countClass(vectors, 0) != 10 {
			t.Fatal("class split is not 10/10")
		}
	}
}

func TestGenerateMLKEMTVLAReproducible(t *testing.T) {
	seed := bytes.Repeat([]byte{0x11}, MLKEM768SeedSize)
	fixedInput := func() []byte {
		v, err := GenerateMLKEMTVLA(MLKEMTVLAConfig{Op: MLKEMDecaps, NumTraces: 4, FixedSeed: seed})
		if err != nil {
			t.Fatal(err)
		}
		for _, x := range v {
			if x.Class == 0 {
				return x.Input
			}
		}
		return nil
	}
	if !bytes.Equal(fixedInput(), fixedInput()) {
		t.Fatal("fixed class (dk||c) differs between runs with the same seed")
	}
}

func TestGenerateMLKEMTVLAInvalid(t *testing.T) {
	for _, n := range []int{0, 2, 7} {
		if _, err := GenerateMLKEMTVLA(MLKEMTVLAConfig{Op: MLKEMKeyGen, NumTraces: n}); err == nil {
			t.Errorf("NumTraces=%d: expected error", n)
		}
	}
	if _, err := GenerateMLKEMTVLA(MLKEMTVLAConfig{NumTraces: 4, FixedSeed: []byte{1}}); err == nil {
		t.Error("short fixed seed: expected error")
	}
	if _, err := ParseMLKEMOp("encaps"); err == nil {
		t.Error("ParseMLKEMOp(encaps): expected error")
	}
}

func countClass(v []MLKEMVector, class int) int {
	n := 0
	for _, x := range v {
		if x.Class == class {
			n++
		}
	}
	return n
}

// gaussTraces draws n single-sample traces from N(mean, 1).
func gaussTraces(r *rand.Rand, n int, mean float64) [][]float64 {
	tr := make([][]float64, n)
	for i := range tr {
		tr[i] = []float64{mean + r.NormFloat64()}
	}
	return tr
}

func TestWelchTTestSameDistribution(t *testing.T) {
	// Both classes from the same distribution: |t| must stay below 4.5.
	r := rand.New(rand.NewPCG(1, 2))
	for trial := 0; trial < 20; trial++ {
		res, err := WelchTTest(gaussTraces(r, 5000, 100), gaussTraces(r, 5000, 100))
		if err != nil {
			t.Fatal(err)
		}
		if res.Leakage {
			t.Fatalf("trial %d: false positive, |t| = %.2f", trial, res.MaxAbsT)
		}
	}
}

func TestWelchTTestGrowsWithTraces(t *testing.T) {
	// A small mean offset (0.1 sigma) is hidden at 400 traces per class and
	// detected at 10000; |t| grows roughly with sqrt(N).
	r := rand.New(rand.NewPCG(3, 4))
	small, _ := WelchTTest(gaussTraces(r, 400, 0.1), gaussTraces(r, 400, 0))
	large, _ := WelchTTest(gaussTraces(r, 10000, 0.1), gaussTraces(r, 10000, 0))
	if small.Leakage || !large.Leakage {
		t.Fatalf("|t| = %.2f at N=400, %.2f at N=10000; want below/above 4.5",
			small.MaxAbsT, large.MaxAbsT)
	}
	// Expected t = 0.1 * sqrt(N/2): 1.4 and 7.1.
	if large.MaxAbsT < 5 || large.MaxAbsT > 10 {
		t.Errorf("|t| = %.2f at N=10000, want about 7", large.MaxAbsT)
	}
}

func TestWelchTTestConstantClasses(t *testing.T) {
	constant := func(n int, v float64) [][]float64 {
		tr := make([][]float64, n)
		for i := range tr {
			tr[i] = []float64{v}
		}
		return tr
	}
	// Constant but different (e.g. two fixed cycle counts): certain leakage.
	res, _ := WelchTTest(constant(10, 110633), constant(10, 110634))
	if !res.Leakage || !math.IsInf(res.TValues[0], -1) {
		t.Errorf("different constants: t = %v, want -Inf and leakage", res.TValues[0])
	}
	// Constant and equal (constant-time): no leakage.
	res, _ = WelchTTest(constant(10, 65779), constant(10, 65779))
	if res.Leakage || res.TValues[0] != 0 {
		t.Errorf("equal constants: t = %v, want 0 and no leakage", res.TValues[0])
	}
}
