package cmd

import (
	"bufio"
	"fmt"
	"math"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/sca"
	"github.com/spf13/cobra"
)

var (
	simBinary    string
	simOut       string
	simNum       int
	simNoise     float64
	simNoiseSeed uint64
	simKeepVCD   int
	simJobs      int
	simScope     string
	simROM       string
	simSanity    bool
)

var scaSimCmd = &cobra.Command{
	Use:   "sim",
	Short: "Simulation-based TVLA: Hamming-distance traces from RTL VCD waveforms",
	Long: `Pre-silicon leakage assessment of the ML-KEM-768 RTL.

For every shuffled fixed/random vector the Verilator model in
testbench/tvla_sim is run once and dumps a VCD of the core. Each VCD is
turned into a simulated power trace: the number of signal bits that toggle
in every clock cycle (Hamming-distance model), for the whole core and for
each top-level instance. Gaussian noise (--noise, in toggles) is added and
Welch's t-test is accumulated per cycle and per instance.

Outputs in --out:
  vectors.csv         the inputs (index,class,input_hex)
  vcd/                the first --keep-vcd VCDs of each class, for GTKWave
  tvla_t.csv          t per cycle: cycle,pc,instruction,t_total,t_<instance>...
  tvla_mean.csv       mean toggles per cycle of both classes (total)
  summary.txt         the report printed at the end

--fixed-vs-fixed gives both classes the same input: a check of the whole
flow, which must not report leakage beyond chance.

Build the model first:  make -C testbench/tvla_sim
Guide:                  docs/SIM_TVLA_GUIDE.md

The core has no masking, so leakage is expected: the result shows where an
unprotected implementation leaks, not that it is secure.`,
	RunE: runSCASim,
}

func init() {
	f := scaSimCmd.Flags()
	f.StringVar(&simBinary, "sim", "testbench/tvla_sim/obj_dir/Vtb_tvla", "Verilator model built by make -C testbench/tvla_sim")
	f.StringVarP(&simOut, "out", "o", "build/tvla_sim", "output directory")
	f.IntVarP(&simNum, "traces", "n", 200, "total traces, split equally between fixed and random")
	f.Float64Var(&simNoise, "noise", 1.0, "standard deviation of Gaussian noise added to each sample (toggles)")
	f.Uint64Var(&simNoiseSeed, "noise-seed", 1, "seed of the noise generator")
	f.IntVar(&simKeepVCD, "keep-vcd", 1, "VCDs kept per class (each Decaps VCD is about 45 MB)")
	f.IntVarP(&simJobs, "jobs", "j", runtime.NumCPU(), "parallel simulations")
	f.StringVar(&simScope, "scope", "tb_tvla.dut", "VCD scope of the core")
	f.StringVar(&simROM, "rom", "hdl/core/mlkem_ucode_rom.sv", "microcode ROM source, used to name instructions")
	f.BoolVar(&simSanity, "fixed-vs-fixed", false, "sanity check: both classes use the fixed input, so no leakage may be reported")
}

type simResult struct {
	index  int
	trace  *sca.VCDTrace
	cycles int
	err    error
}

func runSCASim(cmd *cobra.Command, args []string) error {
	if _, err := os.Stat(simBinary); err != nil {
		return fmt.Errorf("simulator %s not found; build it with: make -C testbench/tvla_sim", simBinary)
	}
	op, vectors, err := scaVectorsN(simNum)
	if err != nil {
		return err
	}
	hwOp := 0
	if op == sca.MLKEMDecaps {
		hwOp = 2
	}
	mode := ""
	if op == sca.MLKEMDecaps && scaInvalidRandom {
		mode = ", random class: random ciphertexts"
	}
	if simSanity {
		mode = ", fixed-vs-fixed sanity check"
		var fixed []byte
		for _, v := range vectors {
			if v.Class == 0 {
				fixed = v.Input
				break
			}
		}
		for i := range vectors {
			vectors[i].Input = fixed
		}
	}
	vcdDir := filepath.Join(simOut, "vcd")
	tmpDir := filepath.Join(simOut, "tmp")
	for _, d := range []string{vcdDir, tmpDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return err
		}
	}
	defer os.RemoveAll(tmpDir)
	if err := writeVectorsCSV(filepath.Join(simOut, "vectors.csv"), vectors); err != nil {
		return err
	}
	rom := readROMText(simROM)

	// Keep the first simKeepVCD VCDs of each class.
	keep := map[int]string{}
	var kept [2]int
	for i, v := range vectors {
		if kept[v.Class] < simKeepVCD {
			kept[v.Class]++
			keep[i] = filepath.Join(vcdDir, fmt.Sprintf("trace_%04d_%s.vcd", i, className(v.Class)))
		}
	}

	fmt.Printf("Simulation TVLA: ML-KEM-768 %s%s, %d traces, noise sigma %.2f, %d jobs\n",
		op, mode, len(vectors), simNoise, simJobs)

	jobs := make(chan int)
	results := make(chan simResult)
	for w := 0; w < max(1, simJobs); w++ {
		go func() {
			for i := range jobs {
				results <- simulateTrace(i, vectors[i].Input, hwOp, keep[i], tmpDir)
			}
		}()
	}
	go func() {
		for i := range vectors {
			jobs <- i
		}
		close(jobs)
	}()

	accs := map[string]*sca.TTestAccumulator{}
	var groups []string
	var pc []uint64
	minCyc, maxCyc := math.MaxInt, 0
	start := time.Now()
	var firstErr error
	for done := 0; done < len(vectors); done++ {
		r := <-results
		if r.err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("trace %d: %w", r.index, r.err)
			}
			continue
		}
		if groups == nil {
			groups = r.trace.Groups
			for _, g := range groups {
				accs[g] = &sca.TTestAccumulator{}
			}
		}
		if r.index == 0 || pc == nil {
			pc = r.trace.Probes["u_ctrl.pc"]
		}
		minCyc, maxCyc = min(minCyc, r.cycles), max(maxCyc, r.cycles)
		noise := rand.New(rand.NewPCG(simNoiseSeed, uint64(r.index)))
		class := vectors[r.index].Class
		for g, name := range r.trace.Groups {
			t := r.trace.Toggles[g]
			if simNoise > 0 {
				for c := range t {
					t[c] += simNoise * noise.NormFloat64()
				}
			}
			accs[name].Add(class, t)
		}
		if n := done + 1; n%max(1, len(vectors)/10) == 0 || n == len(vectors) {
			fmt.Printf("  %5d/%d traces  (%.0f s)\n", n, len(vectors), time.Since(start).Seconds())
		}
	}
	if firstErr != nil {
		return firstErr
	}

	res := map[string]sca.TVLAResult{}
	for _, g := range groups {
		res[g] = accs[g].Result()
	}
	instr := func(c int) (uint64, string) {
		if c < len(pc) {
			return pc[c], rom[pc[c]]
		}
		return 0, ""
	}
	if err := writeSimCSVs(groups, res, accs["total"], instr); err != nil {
		return err
	}

	var sb strings.Builder
	fmt.Fprintf(&sb, "ML-KEM-768 %s simulation TVLA%s\n", op, mode)
	fmt.Fprintf(&sb, "seed: %s\n", scaSeedUsed)
	fmt.Fprintf(&sb, "traces: %d (%d fixed, %d random), cycles per trace: %d..%d, noise sigma: %.2f toggles\n",
		len(vectors), res["total"].NumFixed, res["total"].NumRandom, minCyc, maxCyc, simNoise)
	if minCyc != maxCyc {
		fmt.Fprintf(&sb, "note: trace lengths differ, so cycles after the first data-dependent\n"+
			"      delay are misaligned between traces\n")
	}
	fmt.Fprintf(&sb, "\n%-10s %9s %8s %9s %9s  %s\n", "instance", "max|t|", "cycle", "leaky", "first", "instruction at max")
	for _, g := range groups {
		r := res[g]
		leaky, first := 0, -1
		for c, t := range r.TValues {
			if math.Abs(t) > sca.LeakageThreshold {
				leaky++
				if first < 0 {
					first = c
				}
			}
		}
		_, text := instr(r.MaxAbsTIndex)
		fmt.Fprintf(&sb, "%-10s %9.1f %8d %8.2f%% %9d  %s\n", g, r.MaxAbsT, r.MaxAbsTIndex,
			100*float64(leaky)/float64(max(1, len(r.TValues))), first, text)
	}
	fmt.Fprintf(&sb, "\nleaky = share of cycles with |t| > %.1f; first = first such cycle (-1: none)\n",
		sca.LeakageThreshold)
	sb.WriteString(topInstructions(res["total"].TValues, pc, rom, 10))
	verdict := "no leakage detected"
	switch {
	case res["total"].Leakage && simSanity:
		verdict = "sanity check reports |t| > 4.5: false positives, see max|t| and leaky share"
	case res["total"].Leakage:
		verdict = "LEAKAGE DETECTED (expected for an unmasked core)"
	case simSanity:
		verdict = "sanity check passed: no leakage between identical inputs"
	}
	fmt.Fprintf(&sb, "\nRESULT: %s\n", verdict)
	fmt.Fprintf(&sb, "outputs: %s/{tvla_t.csv,tvla_mean.csv,vectors.csv,vcd/}\n", simOut)

	fmt.Print("\n" + sb.String())
	return os.WriteFile(filepath.Join(simOut, "summary.txt"), []byte(sb.String()), 0o644)
}

func className(c int) string {
	if c == 0 {
		return "fixed"
	}
	return "random"
}

var tvlaResultRE = regexp.MustCompile(`TVLA_RESULT status=(\d+) cycles=(\d+)`)

// simulateTrace runs the Verilator model on one input and parses its VCD.
func simulateTrace(i int, input []byte, op int, keepPath, tmpDir string) simResult {
	memPath := filepath.Join(tmpDir, fmt.Sprintf("in_%04d.mem", i))
	vcdPath := keepPath
	if vcdPath == "" {
		vcdPath = filepath.Join(tmpDir, fmt.Sprintf("trace_%04d.vcd", i))
		defer os.Remove(vcdPath)
	}
	defer os.Remove(memPath)

	var mem strings.Builder
	for _, b := range input {
		fmt.Fprintf(&mem, "%02x\n", b)
	}
	if err := os.WriteFile(memPath, []byte(mem.String()), 0o644); err != nil {
		return simResult{index: i, err: err}
	}
	out, err := exec.Command(simBinary, "+in="+memPath, "+len="+strconv.Itoa(len(input)),
		"+op="+strconv.Itoa(op), "+vcd="+vcdPath).CombinedOutput()
	if err != nil {
		return simResult{index: i, err: fmt.Errorf("simulator: %v\n%s", err, out)}
	}
	m := tvlaResultRE.FindSubmatch(out)
	if m == nil {
		return simResult{index: i, err: fmt.Errorf("simulator printed no TVLA_RESULT:\n%s", out)}
	}
	if status, _ := strconv.Atoi(string(m[1])); status&4 != 0 || status&2 == 0 {
		return simResult{index: i, err: fmt.Errorf("core finished with STATUS=%d (error)", status)}
	}
	cycles, _ := strconv.Atoi(string(m[2]))

	f, err := os.Open(vcdPath)
	if err != nil {
		return simResult{index: i, err: err}
	}
	defer f.Close()
	tr, err := sca.ReadVCDToggles(f, sca.VCDConfig{Root: simScope, Clock: "clk", Probes: []string{"u_ctrl.pc"}})
	if err != nil {
		return simResult{index: i, err: fmt.Errorf("parsing %s: %w", vcdPath, err)}
	}
	return simResult{index: i, trace: tr, cycles: cycles}
}

var romLineRE = regexp.MustCompile(`ADDR_W'\((\d+)\): data <= [^;]*;\s*//\s*(.*)$`)

// readROMText maps microcode addresses to their instruction comments.
func readROMText(path string) map[uint64]string {
	text := map[uint64]string{}
	f, err := os.Open(path)
	if err != nil {
		return text
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if m := romLineRE.FindStringSubmatch(sc.Text()); m != nil {
			pc, _ := strconv.ParseUint(m[1], 10, 64)
			text[pc] = strings.Join(strings.Fields(m[2]), " ")
		}
	}
	return text
}

func writeSimCSVs(groups []string, res map[string]sca.TVLAResult, total *sca.TTestAccumulator,
	instr func(int) (uint64, string)) error {
	header := []string{"cycle", "pc", "instruction"}
	for _, g := range groups {
		header = append(header, "t_"+g)
	}
	rows := [][]string{header}
	n := len(res["total"].TValues)
	for c := 0; c < n; c++ {
		pc, text := instr(c)
		row := []string{strconv.Itoa(c), strconv.FormatUint(pc, 10), text}
		for _, g := range groups {
			t := 0.0
			if c < len(res[g].TValues) {
				t = res[g].TValues[c]
			}
			row = append(row, strconv.FormatFloat(t, 'f', 3, 64))
		}
		rows = append(rows, row)
	}
	if err := writeCSV(filepath.Join(simOut, "tvla_t.csv"), rows); err != nil {
		return err
	}

	mf, mr := total.Mean(0), total.Mean(1)
	rows = [][]string{{"cycle", "mean_fixed", "mean_random"}}
	for c := range mf {
		rows = append(rows, []string{strconv.Itoa(c),
			strconv.FormatFloat(mf[c], 'f', 2, 64), strconv.FormatFloat(mr[c], 'f', 2, 64)})
	}
	return writeCSV(filepath.Join(simOut, "tvla_mean.csv"), rows)
}

// topInstructions ranks microcode instructions by their largest |t|.
func topInstructions(t []float64, pc []uint64, rom map[uint64]string, n int) string {
	type span struct {
		pc          uint64
		first, last int
		maxT        float64
		at          int
	}
	var spans []span
	for c := 0; c < len(t) && c < len(pc); c++ {
		if len(spans) == 0 || spans[len(spans)-1].pc != pc[c] {
			spans = append(spans, span{pc: pc[c], first: c})
		}
		s := &spans[len(spans)-1]
		s.last = c
		if a := math.Abs(t[c]); a > s.maxT {
			s.maxT, s.at = a, c
		}
	}
	sort.SliceStable(spans, func(i, j int) bool { return spans[i].maxT > spans[j].maxT })
	var sb strings.Builder
	fmt.Fprintf(&sb, "\nTop %d microcode instructions by max|t| (total):\n", n)
	fmt.Fprintf(&sb, "%9s %15s %5s  %s\n", "max|t|", "cycles", "pc", "instruction")
	for i := 0; i < n && i < len(spans); i++ {
		s := spans[i]
		fmt.Fprintf(&sb, "%9.1f %7d-%-7d %5d  %s\n", s.maxT, s.first, s.last, s.pc, rom[s.pc])
	}
	return sb.String()
}
