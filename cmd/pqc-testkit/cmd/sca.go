package cmd

import (
	"crypto/rand"
	"encoding/csv"
	"encoding/hex"
	"fmt"
	"math"
	"os"
	"strconv"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/fpga"
	"github.com/akhilesharora/pqc-testkit/pkg/sca"
	"github.com/spf13/cobra"
)

var (
	scaOp            string
	scaNum           int
	scaSeed          string
	scaInvalidRandom bool
	scaOutput        string
	scaSeedUsed      string // Seed of the last generated vector set, as hex.
	scaTransport     string
	scaDevice        string
	scaBaud          int
	scaMapSize       int
	scaTimeout       int
)

var scaCmd = &cobra.Command{
	Use:   "sca",
	Short: "Side-channel (TVLA) test vectors and timing leakage tests for ML-KEM-768",
	Long: `Fixed-vs-random TVLA for the ML-KEM-768 FPGA core.

  gen     write shuffled fixed/random input vectors to a CSV file, for
          trace capture with an external oscilloscope setup
  timing  run the vectors on the FPGA and use the CYCLE_COUNT register as a
          one-sample trace (timing TVLA, Welch's t-test, |t| > 4.5 = leakage)

Operations:
  keygen  fixed seed d||z vs random seeds
  decaps  fixed dk; one fixed valid ciphertext vs fresh valid ciphertexts,
          or vs random ciphertexts (implicit rejection) with --invalid-random`,
}

var scaGenCmd = &cobra.Command{
	Use:   "gen",
	Short: "Generate shuffled ML-KEM-768 fixed-vs-random TVLA vectors",
	Long: `Writes one CSV row per trace: index,class,input_hex, where class 0 is
fixed and 1 is random, and input_hex is the byte string for the core's data
buffer (d||z for keygen, dk||c for decaps). Rows are already in random
order; capture them in file order.`,
	RunE: runSCAGen,
}

var scaTimingCmd = &cobra.Command{
	Use:   "timing",
	Short: "Timing TVLA on the FPGA using CYCLE_COUNT as a one-sample trace",
	Long: `Runs shuffled ML-KEM-768 fixed-vs-random vectors on the FPGA, records the
CYCLE_COUNT of every run and applies Welch's t-test to the two classes.

If every run takes the same number of cycles the core is constant-time for
this input class, which is reported directly (the t-statistic is 0). If
each class is constant but the two differ, |t| is infinite: certain leakage.

Examples:
  pqc-testkit sca timing -T uart -d /dev/cu.usbserial-XXXX1 --op decaps -n 2000
  pqc-testkit sca timing -T uart -d /dev/ttyUSB1 --op decaps --invalid-random -o dec.csv
  pqc-testkit sca timing -T sim --op keygen -n 100`,
	RunE: runSCATiming,
}

func init() {
	for _, c := range []*cobra.Command{scaGenCmd, scaTimingCmd, scaSimCmd} {
		c.Flags().StringVar(&scaOp, "op", "decaps", "operation (keygen, decaps)")
		if c != scaSimCmd {
			c.Flags().IntVarP(&scaNum, "traces", "n", 1000, "total traces, split equally between fixed and random")
		}
		c.Flags().StringVar(&scaSeed, "seed", "", "fixed 64-byte seed as hex (keygen d||z, or decaps key pair); random if empty")
		c.Flags().BoolVar(&scaInvalidRandom, "invalid-random", false, "decaps: random class uses random (invalid) ciphertexts")
	}
	scaGenCmd.Flags().StringVarP(&scaOutput, "output", "o", "tvla_vectors.csv", "output CSV file")
	scaTimingCmd.Flags().StringVarP(&scaOutput, "output", "o", "", "optional CSV of index,class,cycles")

	f := scaTimingCmd.Flags()
	f.StringVarP(&scaTransport, "transport", "T", "uart", "transport layer (pcie, axi, uart, sim)")
	f.StringVarP(&scaDevice, "device", "d", "", "device path (e.g., /dev/ttyUSB1, /dev/xdma0)")
	f.IntVarP(&scaBaud, "baud", "b", 115200, "UART baud rate (only for uart transport)")
	f.IntVar(&scaMapSize, "map-size", 0x10000, "AXI mmap region size in bytes (only for axi transport)")
	f.IntVar(&scaTimeout, "timeout", 5, "operation timeout in seconds")

	scaCmd.AddCommand(scaGenCmd, scaTimingCmd, scaSimCmd)
	rootCmd.AddCommand(scaCmd)
}

// scaVectors builds the vector set from the shared sca flags.
func scaVectors() (sca.MLKEMOp, []sca.MLKEMVector, error) {
	return scaVectorsN(scaNum)
}

// scaVectorsN builds n vectors from the shared sca flags.
func scaVectorsN(n int) (sca.MLKEMOp, []sca.MLKEMVector, error) {
	op, err := sca.ParseMLKEMOp(scaOp)
	if err != nil {
		return 0, nil, err
	}
	cfg := sca.MLKEMTVLAConfig{Op: op, NumTraces: n, InvalidRandom: scaInvalidRandom}
	if scaSeed != "" {
		if cfg.FixedSeed, err = hex.DecodeString(scaSeed); err != nil {
			return 0, nil, fmt.Errorf("--seed: %w", err)
		}
	} else {
		// Pick the seed here so it can be printed and the fixed class
		// reproduced with --seed.
		cfg.FixedSeed = make([]byte, sca.MLKEM768SeedSize)
		if _, err := rand.Read(cfg.FixedSeed); err != nil {
			return 0, nil, err
		}
	}
	scaSeedUsed = hex.EncodeToString(cfg.FixedSeed)
	if scaInvalidRandom && op != sca.MLKEMDecaps {
		return 0, nil, fmt.Errorf("--invalid-random only applies to --op decaps")
	}
	vectors, err := sca.GenerateMLKEMTVLA(cfg)
	return op, vectors, err
}

func runSCAGen(cmd *cobra.Command, args []string) error {
	op, vectors, err := scaVectors()
	if err != nil {
		return err
	}
	if err := writeVectorsCSV(scaOutput, vectors); err != nil {
		return err
	}
	fmt.Printf("wrote %d ML-KEM-768 %s TVLA vectors (%d fixed, %d random, shuffled) to %s\n",
		len(vectors), op, len(vectors)/2, len(vectors)/2, scaOutput)
	fmt.Printf("seed: %s\n(pass --seed with this value to regenerate the same fixed class)\n", scaSeedUsed)
	return nil
}

// writeVectorsCSV writes index,class,input_hex rows.
func writeVectorsCSV(path string, vectors []sca.MLKEMVector) error {
	rows := [][]string{{"index", "class", "input_hex"}}
	for i, v := range vectors {
		rows = append(rows, []string{strconv.Itoa(i), strconv.Itoa(v.Class), hex.EncodeToString(v.Input)})
	}
	return writeCSV(path, rows)
}

// writeCSV writes all rows to a new file.
func writeCSV(path string, rows [][]string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	w := csv.NewWriter(f)
	w.WriteAll(rows)
	if err := w.Error(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

func runSCATiming(cmd *cobra.Command, args []string) error {
	if scaDevice == "" && scaTransport != "sim" {
		return fmt.Errorf("--device is required (or use --transport sim)")
	}
	op, vectors, err := scaVectors()
	if err != nil {
		return err
	}
	timeout := time.Duration(scaTimeout) * time.Second

	dev, err := openTransport(scaTransport, scaDevice, scaBaud, scaMapSize)
	if err != nil {
		return fmt.Errorf("opening device: %w", err)
	}
	defer dev.Close()
	info, err := fpga.Identify(dev)
	if err != nil {
		return err
	}
	if info.AlgorithmID != fpga.AlgMLKEM {
		return fmt.Errorf("core is %s, want ML-KEM", info.Algorithm)
	}

	hwOp := uint32(fpga.OpKeyGen)
	if op == sca.MLKEMDecaps {
		hwOp = fpga.OpDecaps
	}
	for _, w := range []struct{ reg, val uint32 }{
		{fpga.RegCTRL, fpga.CtrlReset},
		{fpga.RegDataInAddr, mlkemInOffset},
		{fpga.RegDataInLen, uint32(len(vectors[0].Input))},
		{fpga.RegDataOutAddr, mlkemOutOffset},
		{fpga.RegSecLevel, 768},
		{fpga.RegOpMode, hwOp},
	} {
		if err := dev.WriteReg(w.reg, w.val); err != nil {
			return err
		}
	}

	mode := ""
	if op == sca.MLKEMDecaps {
		mode = " (random class: valid ciphertexts)"
		if scaInvalidRandom {
			mode = " (random class: random ciphertexts, implicit rejection)"
		}
	}
	fmt.Printf("Timing TVLA: ML-KEM-768 %s%s, %d traces via %s\n", op, mode, len(vectors), dev.Transport())
	fmt.Printf("seed: %s\n", scaSeedUsed)

	cycles := make([]uint32, len(vectors))
	var prev []byte
	start := time.Now()
	for i, v := range vectors {
		// The core never writes the input region, so only the bytes that
		// differ from the previous input are sent (dk stays in place).
		if lo, hi := diffRange(prev, v.Input); lo < hi {
			if err := dev.WriteData(mlkemInOffset+uint32(lo), v.Input[lo:hi]); err != nil {
				return fmt.Errorf("trace %d: %w", i, err)
			}
		}
		prev = v.Input
		if err := dev.WriteReg(fpga.RegCTRL, fpga.CtrlStart); err != nil {
			return fmt.Errorf("trace %d: %w", i, err)
		}
		c, err := fpga.WaitDone(dev, timeout)
		if err != nil {
			return fmt.Errorf("trace %d (class %d): %w", i, v.Class, err)
		}
		cycles[i] = c
		if n := i + 1; n%max(1, len(vectors)/10) == 0 || n == len(vectors) {
			fmt.Printf("  %5d/%d traces  (%.0f s)\n", n, len(vectors), time.Since(start).Seconds())
		}
	}

	if scaOutput != "" {
		if err := writeTimingCSV(scaOutput, vectors, cycles); err != nil {
			return err
		}
		fmt.Printf("cycle counts written to %s\n", scaOutput)
	}

	var fixed, random [][]float64
	for i, v := range vectors {
		t := []float64{float64(cycles[i])}
		if v.Class == 0 {
			fixed = append(fixed, t)
		} else {
			random = append(random, t)
		}
	}
	res, err := sca.WelchTTest(fixed, random)
	if err != nil {
		return err
	}

	fmt.Printf("\n%-8s %6s %10s %10s %12s %10s\n", "class", "n", "min", "max", "mean", "std")
	printCycleStats("fixed", fixed)
	printCycleStats("random", random)
	fmt.Printf("\nWelch t = %.3f  (threshold |t| = 4.5)\n", res.TValues[0])

	fmin, fmax := minMax(fixed)
	rmin, rmax := minMax(random)
	switch {
	case res.Leakage:
		fmt.Printf("RESULT: TIMING LEAKAGE - cycle count depends on the %s input class\n", op)
		if op == sca.MLKEMKeyGen {
			fmt.Println("NOTE: KeyGen time varies with the rejection sampling of A from rho, which is")
			fmt.Println("      public (part of ek), so this difference alone does not leak secrets.")
			fmt.Println("      The secret-dependent test is --op decaps.")
		}
		return fmt.Errorf("timing leakage detected (|t| = %.3g)", res.MaxAbsT)
	case fmin == fmax && rmin == rmax && fmin == rmin:
		fmt.Printf("RESULT: PASS - constant time, all %d runs took %.0f cycles\n", len(vectors), fmin)
	default:
		fmt.Printf("RESULT: PASS - no timing leakage detected at %d traces\n", len(vectors))
	}
	return nil
}

// diffRange returns the byte range [lo, hi) where cur differs from prev.
// A nil or differently sized prev yields the whole of cur.
func diffRange(prev, cur []byte) (int, int) {
	if len(prev) != len(cur) {
		return 0, len(cur)
	}
	lo, hi := 0, len(cur)
	for lo < hi && prev[lo] == cur[lo] {
		lo++
	}
	for hi > lo && prev[hi-1] == cur[hi-1] {
		hi--
	}
	return lo, hi
}

func writeTimingCSV(path string, vectors []sca.MLKEMVector, cycles []uint32) error {
	rows := [][]string{{"index", "class", "cycles"}}
	for i, v := range vectors {
		rows = append(rows, []string{strconv.Itoa(i), strconv.Itoa(v.Class), strconv.FormatUint(uint64(cycles[i]), 10)})
	}
	return writeCSV(path, rows)
}

func minMax(traces [][]float64) (float64, float64) {
	lo, hi := math.Inf(1), math.Inf(-1)
	for _, t := range traces {
		lo, hi = math.Min(lo, t[0]), math.Max(hi, t[0])
	}
	return lo, hi
}

func printCycleStats(name string, traces [][]float64) {
	var sum, sq float64
	for _, t := range traces {
		sum += t[0]
	}
	mean := sum / float64(len(traces))
	for _, t := range traces {
		sq += (t[0] - mean) * (t[0] - mean)
	}
	std := 0.0
	if len(traces) > 1 {
		std = math.Sqrt(sq / float64(len(traces)-1))
	}
	lo, hi := minMax(traces)
	fmt.Printf("%-8s %6d %10.0f %10.0f %12.1f %10.2f\n", name, len(traces), lo, hi, mean, std)
}
