package cmd

import (
	"fmt"
	"strings"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/algorithms"
	"github.com/akhilesharora/pqc-testkit/pkg/kat"
	"github.com/spf13/cobra"
)

var (
	katAlgorithm string
	katVectorDir string
	katLevel     int
)

var katCmd = &cobra.Command{
	Use:   "kat",
	Short: "Run Known Answer Test vector validation",
	Long: `Validate PQC implementations against NIST KAT vectors.

Loads test vectors from the specified directory and runs keygen,
encapsulation/signing, and decapsulation/verification operations,
comparing outputs against expected values.`,
	RunE: runKAT,
}

func init() {
	katCmd.Flags().StringVarP(&katAlgorithm, "algorithm", "a", "ml-kem", "algorithm to test (ml-kem, ml-dsa, slh-dsa)")
	katCmd.Flags().StringVarP(&katVectorDir, "vectors", "v", "vectors/", "path to KAT vector directory")
	katCmd.Flags().IntVarP(&katLevel, "level", "l", 0, "security level (0=all, 512/768/1024 for ML-KEM, 44/65/87 for ML-DSA)")
	rootCmd.AddCommand(katCmd)
}

func runKAT(cmd *cobra.Command, args []string) error {
	algo := strings.ToLower(katAlgorithm)

	suite, err := algorithms.NewSuite(algo)
	if err != nil {
		return fmt.Errorf("unsupported algorithm %q: %w", algo, err)
	}

	levels := suite.Levels()
	if katLevel != 0 {
		if !suite.SupportsLevel(katLevel) {
			return fmt.Errorf("algorithm %s does not support level %d", algo, katLevel)
		}
		levels = []int{katLevel}
	}

	fmt.Printf("PQC Test Kit — KAT Validation\n")
	fmt.Printf("Algorithm: %s\n", suite.Name())
	fmt.Printf("Levels:    %v\n\n", levels)

	totalPass, totalFail := 0, 0

	for _, level := range levels {
		vectors, err := kat.LoadVectors(katVectorDir, algo, level)
		if err != nil {
			fmt.Printf("[SKIP] Level %d: %v\n", level, err)
			continue
		}

		fmt.Printf("--- Level %d (%d vectors) ---\n", level, len(vectors))
		pass, fail := 0, 0
		start := time.Now()

		for i, vec := range vectors {
			result, err := suite.RunKAT(level, vec)
			if err != nil {
				fmt.Printf("  [FAIL] Vector %d: %v\n", i, err)
				fail++
				continue
			}
			if !result.Match {
				fmt.Printf("  [FAIL] Vector %d: output mismatch at %s\n", i, result.MismatchField)
				fail++
				continue
			}
			pass++
		}

		elapsed := time.Since(start)
		fmt.Printf("  Result: %d passed, %d failed (%.2fs)\n\n", pass, fail, elapsed.Seconds())
		totalPass += pass
		totalFail += fail
	}

	fmt.Printf("Total: %d passed, %d failed\n", totalPass, totalFail)
	if totalFail > 0 {
		return fmt.Errorf("%d KAT vectors failed", totalFail)
	}
	return nil
}
