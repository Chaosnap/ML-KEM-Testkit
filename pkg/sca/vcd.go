package sca

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
)

// VCDConfig controls the conversion of a VCD waveform into a simulated
// power trace.
type VCDConfig struct {
	// Root is the hierarchical scope of the design under test, e.g.
	// "tb_tvla.dut". Signals outside it are ignored.
	Root string

	// Clock is the name of the clock net directly inside Root. Each rising
	// edge starts a new sample (cycle); the clock itself is not counted.
	Clock string

	// Probes are signals, relative to Root (e.g. "u_ctrl.pc"), whose value
	// at the end of every cycle is recorded. Width must be at most 64 bits.
	Probes []string
}

// VCDTrace is a Hamming-distance power model extracted from one VCD: the
// number of signal bits that toggled in each clock cycle.
type VCDTrace struct {
	// Groups names the rows of Toggles: "total" (every net once), "top"
	// (nets declared directly in Root) and one entry per instance directly
	// below Root, which includes everything nested inside that instance.
	// A net connecting two instances counts in both of their groups.
	Groups []string

	// Toggles[g][c] is the Hamming distance of group g in cycle c.
	Toggles [][]float64

	// Probes maps each VCDConfig.Probes name to its per-cycle values.
	Probes map[string][]uint64
}

// Cycles returns the number of clock cycles in the trace.
func (t *VCDTrace) Cycles() int { return len(t.Toggles[0]) }

type vcdSignal struct {
	width  int
	value  []byte // Current value, one character per bit, MSB first.
	groups []int  // Indices into Groups, "total" included.
	probe  string // Probe name, if sampled.
}

// ReadVCDToggles parses a VCD stream and returns the per-cycle toggle
// counts. The first value block (the initial dump) sets the starting
// values and is not counted; toggles before the first rising clock edge
// are dropped.
func ReadVCDToggles(r io.Reader, cfg VCDConfig) (*VCDTrace, error) {
	br := bufio.NewReaderSize(r, 1<<20)
	sigs, groups, clockID, err := readVCDHeader(br, cfg)
	if err != nil {
		return nil, err
	}

	tr := &VCDTrace{Groups: groups, Toggles: make([][]float64, len(groups)), Probes: map[string][]uint64{}}
	probeSigs := map[string]*vcdSignal{}
	for _, s := range sigs {
		if s.probe != "" {
			probeSigs[s.probe] = s
		}
	}
	pending := make([]int, len(groups)) // Toggles in the current time block.
	cycle := -1
	baseline := true // First value block holds initial values.
	sawTime := false
	clockRose := false
	dumping := true

	endBlock := func() {
		if clockRose {
			cycle++
			for g := range tr.Toggles {
				tr.Toggles[g] = append(tr.Toggles[g], 0)
			}
			for name := range probeSigs {
				tr.Probes[name] = append(tr.Probes[name], 0)
			}
		}
		if cycle >= 0 {
			if !baseline {
				for g, n := range pending {
					tr.Toggles[g][cycle] += float64(n)
				}
			}
			// Value at the end of the cycle (last block before the next edge).
			for name, s := range probeSigs {
				tr.Probes[name][cycle] = bitsValue(s.value)
			}
		}
		for g := range pending {
			pending[g] = 0
		}
		clockRose = false
	}

	for {
		line, err := br.ReadSlice('\n')
		if err == bufio.ErrBufferFull {
			return nil, fmt.Errorf("VCD line longer than %d bytes", br.Size())
		}
		line = bytes.TrimSpace(line)
		if len(line) > 0 {
			switch c := line[0]; {
			case c == '#':
				if sawTime {
					endBlock()
					baseline = false
				}
				sawTime = true
			case c == '$':
				switch {
				case bytes.HasPrefix(line, []byte("$dumpoff")):
					dumping = false
				case bytes.HasPrefix(line, []byte("$dumpon")):
					dumping = true
				}
			case c == '0' || c == '1' || c == 'x' || c == 'X' || c == 'z' || c == 'Z':
				if dumping {
					clockRose = applyChange(sigs, string(line[1:]), line[:1], clockID, pending) || clockRose
				}
			case c == 'b' || c == 'B':
				if sp := bytes.IndexByte(line, ' '); sp > 0 && dumping {
					applyChange(sigs, string(bytes.TrimSpace(line[sp+1:])), line[1:sp], clockID, pending)
				}
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
	}
	endBlock()
	if cycle < 0 {
		return nil, fmt.Errorf("no rising edge of clock %q in the VCD", cfg.Clock)
	}
	return tr, nil
}

// applyChange updates one signal and adds its Hamming distance to pending.
// It reports whether the change was a rising edge of the clock.
func applyChange(sigs map[string]*vcdSignal, id string, val []byte, clockID string, pending []int) bool {
	s, ok := sigs[id]
	if !ok {
		return false
	}
	if id == clockID {
		rose := s.value[0] != '1' && val[0] == '1'
		s.value[0] = val[0]
		return rose
	}
	// Left-extend short vectors: with 0, or with x/z if that is the MSB.
	pad := byte('0')
	if val[0] == 'x' || val[0] == 'X' || val[0] == 'z' || val[0] == 'Z' {
		pad = val[0]
	}
	hd := 0
	off := s.width - len(val)
	for i := 0; i < s.width; i++ {
		b := pad
		if i >= off {
			b = val[i-off]
		}
		if s.value[i] != b {
			hd++
			s.value[i] = b
		}
	}
	for _, g := range s.groups {
		pending[g] += hd
	}
	return false
}

// bitsValue converts an MSB-first bit string to an integer (x/z read as 0).
func bitsValue(bits []byte) uint64 {
	var v uint64
	for _, b := range bits {
		v <<= 1
		if b == '1' {
			v |= 1
		}
	}
	return v
}

// readVCDHeader parses the declarations up to $enddefinitions.
func readVCDHeader(br *bufio.Reader, cfg VCDConfig) (map[string]*vcdSignal, []string, string, error) {
	root := strings.Split(cfg.Root, ".")
	probes := map[string]bool{}
	for _, p := range cfg.Probes {
		probes[p] = true
	}
	type decl struct {
		id, path string
		width    int
	}
	var decls []decl
	var scope []string

	tok := func() (string, error) {
		var sb strings.Builder
		for {
			c, err := br.ReadByte()
			if err != nil {
				if sb.Len() > 0 && err == io.EOF {
					return sb.String(), nil
				}
				return "", err
			}
			if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
				if sb.Len() > 0 {
					return sb.String(), nil
				}
				continue
			}
			sb.WriteByte(c)
		}
	}
	skipToEnd := func() error {
		for {
			t, err := tok()
			if err != nil {
				return err
			}
			if t == "$end" {
				return nil
			}
		}
	}

	for done := false; !done; {
		t, err := tok()
		if err != nil {
			return nil, nil, "", fmt.Errorf("reading VCD header: %w", err)
		}
		switch t {
		case "$scope":
			if _, err := tok(); err != nil { // Scope type.
				return nil, nil, "", err
			}
			name, err := tok()
			if err != nil {
				return nil, nil, "", err
			}
			scope = append(scope, name)
			if err := skipToEnd(); err != nil {
				return nil, nil, "", err
			}
		case "$upscope":
			if len(scope) > 0 {
				scope = scope[:len(scope)-1]
			}
			if err := skipToEnd(); err != nil {
				return nil, nil, "", err
			}
		case "$var":
			var f [4]string // type, width, id, name
			for i := range f {
				if f[i], err = tok(); err != nil {
					return nil, nil, "", err
				}
			}
			if err := skipToEnd(); err != nil { // Optional bit range.
				return nil, nil, "", err
			}
			if !hasPrefix(scope, root) {
				continue
			}
			w, err := strconv.Atoi(f[1])
			if err != nil || w <= 0 {
				continue // real, event, ...
			}
			rel := append(append([]string(nil), scope[len(root):]...), f[3])
			decls = append(decls, decl{f[2], strings.Join(rel, "."), w})
		case "$enddefinitions":
			if err := skipToEnd(); err != nil {
				return nil, nil, "", err
			}
			done = true
		default:
			if strings.HasPrefix(t, "$") {
				if err := skipToEnd(); err != nil {
					return nil, nil, "", err
				}
			}
		}
	}

	// Groups: total, top, then instances in sorted order. Unnamed
	// procedural-block scopes directly in Root belong to "top".
	group := func(path string) string {
		i := strings.IndexByte(path, '.')
		if i <= 0 || strings.HasPrefix(path, "unnamedblk") {
			return "top"
		}
		return path[:i]
	}
	instSet := map[string]bool{}
	for _, d := range decls {
		if g := group(d.path); g != "top" {
			instSet[g] = true
		}
	}
	insts := make([]string, 0, len(instSet))
	for n := range instSet {
		insts = append(insts, n)
	}
	sort.Strings(insts)
	groups := append([]string{"total", "top"}, insts...)
	index := map[string]int{}
	for i, g := range groups {
		index[g] = i
	}

	sigs := map[string]*vcdSignal{}
	clockID := ""
	for _, d := range decls {
		g := index[group(d.path)]
		s, ok := sigs[d.id]
		if !ok {
			s = &vcdSignal{width: d.width, value: bytes.Repeat([]byte{'x'}, d.width), groups: []int{0}}
			sigs[d.id] = s
		}
		if !containsInt(s.groups, g) {
			s.groups = append(s.groups, g)
		}
		if d.path == cfg.Clock {
			clockID = d.id
		}
		if probes[d.path] {
			if d.width > 64 {
				return nil, nil, "", fmt.Errorf("probe %s is %d bits wide (max 64)", d.path, d.width)
			}
			s.probe = d.path
			delete(probes, d.path)
		}
	}
	if len(decls) == 0 {
		return nil, nil, "", fmt.Errorf("no signals under scope %q", cfg.Root)
	}
	if clockID == "" {
		return nil, nil, "", fmt.Errorf("clock %q not found in scope %q", cfg.Clock, cfg.Root)
	}
	for p := range probes {
		return nil, nil, "", fmt.Errorf("probe %q not found in scope %q", p, cfg.Root)
	}
	return sigs, groups, clockID, nil
}

func hasPrefix(scope, root []string) bool {
	if len(scope) < len(root) {
		return false
	}
	for i := range root {
		if scope[i] != root[i] {
			return false
		}
	}
	return true
}

func containsInt(v []int, x int) bool {
	for _, y := range v {
		if y == x {
			return true
		}
	}
	return false
}
