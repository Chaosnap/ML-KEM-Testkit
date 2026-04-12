package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

var (
	twidOutDir string
)

var gentwidCmd = &cobra.Command{
	Use:   "gen-twiddle",
	Short: "Generate NTT twiddle factor hex files for FPGA synthesis",
	Long: `Computes roots of unity mod q for the NTT and writes them as hex files
that the HDL ntt_engine.sv loads via $readmemh.

Generates twiddle factors for:
  - ML-KEM:  q=3329,    primitive 2n-th root of unity (n=256)
  - ML-DSA:  q=8380417, primitive 2n-th root of unity (n=256)

Output files:
  twiddle_ntt_3329.hex     Forward NTT twiddles for ML-KEM
  twiddle_intt_3329.hex    Inverse NTT twiddles for ML-KEM
  twiddle_ntt_8380417.hex  Forward NTT twiddles for ML-DSA
  twiddle_intt_8380417.hex Inverse NTT twiddles for ML-DSA`,
	RunE: runGenTwiddle,
}

func init() {
	gentwidCmd.Flags().StringVarP(&twidOutDir, "output", "o", "hdl/core", "output directory for hex files")
	rootCmd.AddCommand(gentwidCmd)
}

// modpow computes base^exp mod m using binary exponentiation.
func modpow(base, exp, m int64) int64 {
	result := int64(1)
	base = base % m
	for exp > 0 {
		if exp%2 == 1 {
			result = (result * base) % m
		}
		exp /= 2
		base = (base * base) % m
	}
	return result
}

// modinv computes the modular inverse of a mod m using Fermat's little theorem
// (m must be prime).
func modinv(a, m int64) int64 {
	return modpow(a, m-2, m)
}

// findPrimitiveRoot finds a primitive 2n-th root of unity mod q.
// For NTT with negative wrapped convolution, we need psi such that
// psi^(2n) = 1 mod q and psi^n = -1 mod q.
func findPrimitiveRoot(q int64, n int) int64 {
	// For ML-KEM (q=3329): the primitive 512th root of unity is 17.
	// For ML-DSA (q=8380417): the primitive 512th root of unity is 1753.
	// These are well-known constants from the NIST specifications.
	if q == 3329 && n == 256 {
		return 17
	}
	if q == 8380417 && n == 256 {
		return 1753
	}

	// Generic search for other parameters.
	twoN := int64(2 * n)
	for g := int64(2); g < q; g++ {
		// Check that g^(2n) = 1 mod q.
		if modpow(g, twoN, q) != 1 {
			continue
		}
		// Check that g^n = q-1 mod q (i.e., -1 mod q).
		if modpow(g, int64(n), q) != q-1 {
			continue
		}
		return g
	}
	return 0
}

// generateTwiddles computes the NTT twiddle factors (powers of psi in
// bit-reversed order) for a given modulus and transform size.
func generateTwiddles(q int64, n int) (forward, inverse []int64) {
	psi := findPrimitiveRoot(q, n)
	psiInv := modinv(psi, q)
	nInv := modinv(int64(n), q)

	forward = make([]int64, n)
	inverse = make([]int64, n)

	// Compute powers of psi for forward NTT.
	// In the standard iterative NTT, twiddle[i] = psi^(bit_reverse(i)).
	for i := 0; i < n; i++ {
		br := bitReverse(i, log2(n))
		forward[i] = modpow(psi, int64(br), q)
	}

	// Compute powers of psi^-1 for inverse NTT, scaled by n^-1.
	for i := 0; i < n; i++ {
		br := bitReverse(i, log2(n))
		inverse[i] = (modpow(psiInv, int64(br), q) * nInv) % q
	}

	return forward, inverse
}

// bitReverse reverses the bottom 'bits' bits of val.
func bitReverse(val, bits int) int {
	result := 0
	for i := 0; i < bits; i++ {
		result = (result << 1) | (val & 1)
		val >>= 1
	}
	return result
}

// log2 returns floor(log2(n)) for positive n.
func log2(n int) int {
	r := 0
	for n > 1 {
		n >>= 1
		r++
	}
	return r
}

// writeHexFile writes twiddle factors as a hex file for $readmemh.
// Each line is one hex value (no prefix).
func writeHexFile(path string, values []int64, width int) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	fmtStr := fmt.Sprintf("%%0%dx\n", (width+3)/4) // Hex digits needed.
	for _, v := range values {
		fmt.Fprintf(f, fmtStr, v)
	}
	return nil
}

func runGenTwiddle(cmd *cobra.Command, args []string) error {
	configs := []struct {
		name  string
		q     int64
		n     int
		width int // Bit width for hex output.
	}{
		{"3329", 3329, 256, 12},
		{"8380417", 8380417, 256, 23},
	}

	for _, cfg := range configs {
		fmt.Printf("Generating twiddle factors for q=%d, n=%d...\n", cfg.q, cfg.n)

		psi := findPrimitiveRoot(cfg.q, cfg.n)
		if psi == 0 {
			return fmt.Errorf("no primitive root found for q=%d, n=%d", cfg.q, cfg.n)
		}
		fmt.Printf("  Primitive 2n-th root of unity (psi): %d\n", psi)

		fwd, inv := generateTwiddles(cfg.q, cfg.n)

		// Verify: psi^(2n) should be 1 mod q.
		check := modpow(psi, int64(2*cfg.n), cfg.q)
		if check != 1 {
			return fmt.Errorf("verification failed: psi^(2n) = %d, expected 1", check)
		}

		fwdPath := filepath.Join(twidOutDir, fmt.Sprintf("twiddle_ntt_%s.hex", cfg.name))
		invPath := filepath.Join(twidOutDir, fmt.Sprintf("twiddle_intt_%s.hex", cfg.name))

		if err := writeHexFile(fwdPath, fwd, cfg.width); err != nil {
			return fmt.Errorf("writing %s: %w", fwdPath, err)
		}
		if err := writeHexFile(invPath, inv, cfg.width); err != nil {
			return fmt.Errorf("writing %s: %w", invPath, err)
		}

		fmt.Printf("  Forward: %s (%d entries)\n", fwdPath, len(fwd))
		fmt.Printf("  Inverse: %s (%d entries)\n", invPath, len(inv))
	}

	// Also generate the default files expected by ntt_engine.sv.
	fmt.Printf("\nCreating symlinks for default NTT engine files...\n")
	for _, pair := range []struct{ src, dst string }{
		{"twiddle_ntt_3329.hex", "twiddle_ntt.hex"},
		{"twiddle_intt_3329.hex", "twiddle_intt.hex"},
	} {
		src := filepath.Join(twidOutDir, pair.src)
		dst := filepath.Join(twidOutDir, pair.dst)
		// Copy instead of symlink for portability.
		data, err := os.ReadFile(src)
		if err != nil {
			return fmt.Errorf("reading %s: %w", src, err)
		}
		if err := os.WriteFile(dst, data, 0644); err != nil {
			return fmt.Errorf("writing %s: %w", dst, err)
		}
		fmt.Printf("  %s -> %s\n", pair.src, pair.dst)
	}

	fmt.Printf("\nDone. Place hex files in Vivado/Quartus project directory for synthesis.\n")
	return nil
}
