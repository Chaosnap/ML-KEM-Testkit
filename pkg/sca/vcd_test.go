package sca

import (
	"math"
	"math/rand/v2"
	"strings"
	"testing"
)

// Clock period 10, rising edges at 10, 20, 30. Registers change on the
// rising edge (same time block, listed before or after the clock), inputs
// on the falling edge.
const testVCD = `$timescale 1ps $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " ignored $end
$scope module dut $end
$var wire 1 ! clk $end
$var wire 4 # state [3:0] $end
$scope module u_a $end
$var wire 1 ! clk $end
$var wire 8 $ r [7:0] $end
$var wire 4 # state_in [3:0] $end
$upscope $end
$scope module u_b $end
$var wire 3 % q [2:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0 "
b0000 #
b00000000 $
b000 %
#5
b11111111 "
b111 %
#10
1!
b0011 #
b1111 $
#15
0!
#20
b10000000 $
1!
#25
0!
b011 %
#30
1!
`

func TestReadVCDToggles(t *testing.T) {
	tr, err := ReadVCDToggles(strings.NewReader(testVCD), VCDConfig{
		Root: "tb.dut", Clock: "clk", Probes: []string{"state"},
	})
	if err != nil {
		t.Fatal(err)
	}
	wantGroups := []string{"total", "top", "u_a", "u_b"}
	if strings.Join(tr.Groups, ",") != strings.Join(wantGroups, ",") {
		t.Fatalf("groups %v, want %v", tr.Groups, wantGroups)
	}
	if tr.Cycles() != 3 {
		t.Fatalf("%d cycles, want 3", tr.Cycles())
	}
	// Cycle 0 (edge at 10): state 0000->0011 (2), r 00->0f (4).
	// Cycle 1 (edge at 20): r 0f->80 (5); q 111->011 at 25 (1).
	// Cycle 2 (edge at 30): nothing.
	// The q change at #5 precedes the first edge and is dropped; the change
	// of the out-of-scope "ignored" net never counts. state is shared by
	// top and u_a but counted once in total.
	want := map[string][]float64{
		"total": {6, 6, 0},
		"top":   {2, 0, 0},
		"u_a":   {6, 5, 0},
		"u_b":   {0, 1, 0},
	}
	for g, name := range tr.Groups {
		for c, v := range want[name] {
			if tr.Toggles[g][c] != v {
				t.Errorf("%s cycle %d: %v toggles, want %v", name, c, tr.Toggles[g][c], v)
			}
		}
	}
	if got := tr.Probes["state"]; len(got) != 3 || got[0] != 3 || got[2] != 3 {
		t.Errorf("state probe %v, want [3 3 3]", got)
	}
}

func TestReadVCDTogglesErrors(t *testing.T) {
	for _, cfg := range []VCDConfig{
		{Root: "tb.nope", Clock: "clk"},
		{Root: "tb.dut", Clock: "clock"},
		{Root: "tb.dut", Clock: "clk", Probes: []string{"u_a.missing"}},
	} {
		if _, err := ReadVCDToggles(strings.NewReader(testVCD), cfg); err == nil {
			t.Errorf("%+v: expected error", cfg)
		}
	}
}

func TestTTestAccumulatorMatchesWelch(t *testing.T) {
	r := rand.New(rand.NewPCG(5, 6))
	var fixed, random [][]float64
	var acc TTestAccumulator
	for i := 0; i < 300; i++ {
		f := []float64{r.NormFloat64(), 3 + r.NormFloat64(), 7}
		x := []float64{r.NormFloat64(), 2 + 2*r.NormFloat64(), 7}
		fixed, random = append(fixed, f), append(random, x)
		acc.Add(0, f)
		acc.Add(1, x)
	}
	want, _ := WelchTTest(fixed, random)
	got := acc.Result()
	for s := range want.TValues {
		if math.Abs(got.TValues[s]-want.TValues[s]) > 1e-9 {
			t.Errorf("sample %d: t = %v, want %v", s, got.TValues[s], want.TValues[s])
		}
	}
	if got.MaxAbsTIndex != want.MaxAbsTIndex || got.Leakage != want.Leakage ||
		got.NumFixed != 300 || got.NumRandom != 300 {
		t.Errorf("result %+v, want %+v", got, want)
	}
	if err := acc.Add(2, nil); err == nil {
		t.Error("class 2: expected error")
	}
}

func TestTTestAccumulatorUnequalLengths(t *testing.T) {
	var acc TTestAccumulator
	acc.Add(0, []float64{1, 2})
	acc.Add(0, []float64{1, 4, 9})
	acc.Add(1, []float64{1, 2, 9})
	acc.Add(1, []float64{1, 4})
	res := acc.Result()
	if acc.Len() != 3 || len(res.TValues) != 3 {
		t.Fatalf("length %d, want 3", acc.Len())
	}
	// Sample 2 has one trace per class: not enough for a variance.
	if res.TValues[2] != 0 {
		t.Errorf("sample 2: t = %v, want 0", res.TValues[2])
	}
}
