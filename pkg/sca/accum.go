package sca

import "fmt"

// TTestAccumulator computes Welch's t-test incrementally, one trace at a
// time (Welford's algorithm), so that large trace sets never have to be held
// in memory. For the same traces it gives the same result as WelchTTest.
//
// Traces may differ in length (e.g. data-dependent run times): every sample
// index keeps its own count, and indices beyond the end of a trace are
// simply not updated by it.
type TTestAccumulator struct {
	n, mean, m2 [2][]float64 // Per class, per sample.
	traces      [2]int
}

// Add adds one trace of the given class (0 = fixed, 1 = random).
func (a *TTestAccumulator) Add(class int, trace []float64) error {
	if class != 0 && class != 1 {
		return fmt.Errorf("class must be 0 or 1, got %d", class)
	}
	if len(trace) > len(a.n[0]) {
		for c := range 2 {
			a.n[c] = grow(a.n[c], len(trace))
			a.mean[c] = grow(a.mean[c], len(trace))
			a.m2[c] = grow(a.m2[c], len(trace))
		}
	}
	n, mean, m2 := a.n[class], a.mean[class], a.m2[class]
	for s, x := range trace {
		n[s]++
		d := x - mean[s]
		mean[s] += d / n[s]
		m2[s] += d * (x - mean[s])
	}
	a.traces[class]++
	return nil
}

// Len returns the number of sample points (the longest trace added).
func (a *TTestAccumulator) Len() int { return len(a.n[0]) }

// Mean returns the per-sample mean trace of a class.
func (a *TTestAccumulator) Mean(class int) []float64 {
	return append([]float64(nil), a.mean[class]...)
}

// Result returns the t-statistic at every sample point. Samples with fewer
// than two traces in either class get t = 0.
func (a *TTestAccumulator) Result() TVLAResult {
	t := make([]float64, a.Len())
	for s := range t {
		nf, nr := a.n[0][s], a.n[1][s]
		if nf < 2 || nr < 2 {
			continue
		}
		t[s] = welchT(a.mean[0][s], a.m2[0][s]/(nf-1), nf, a.mean[1][s], a.m2[1][s]/(nr-1), nr)
	}
	return newTVLAResult(t, a.traces[0], a.traces[1])
}

func grow(v []float64, n int) []float64 {
	return append(v, make([]float64, n-len(v))...)
}
