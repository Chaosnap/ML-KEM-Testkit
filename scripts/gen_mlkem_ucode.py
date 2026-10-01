#!/usr/bin/env python3
"""Generate the ML-KEM core's microcode ROM and NTT constant ROM.

Writes:
  hdl/core/mlkem_ucode_rom.sv  - FIPS 203 KeyGen/Encaps/Decaps programs
  hdl/core/mlkem_zetas.sv      - zeta / gamma constants (FIPS 203 Appendix A)

Usage:
  python3 scripts/gen_mlkem_ucode.py [--levels 768] [output_dir]

The core supports ML-KEM-768 only: the ROM holds the 768 KeyGen / Encaps /
Decaps programs and mlkem_ctrl answers any other SEC_LEVEL with ERROR_CODE 1.
--levels is kept for scripts that pass it and accepts only 768. The ROM is
small enough for distributed (LUT) ROM; it is marked rom_style = "distributed".

Microcode word (96 bits), executed by hdl/core/mlkem_ctrl.sv:
  [95:88] op  [87:80] p  [79:64] a  [63:48] b  [47:32] c  [31:16] len  [15:0] 0

Buffer operands (HABS, HSQZ, COPY, CMP, CSEL, DECODE) must be 32-bit word
aligned with lengths that are multiples of 4: the sequencer moves one word
per buffer access. DECODE carries its byte count (32 * d) in len.

Buffer addresses are tagged: bits [15:14] select the base that the
sequencer adds to the 14-bit offset (0 = absolute, 1 = DATA_IN_ADDR,
2 = DATA_OUT_ADDR).
"""

import argparse
import os

Q = 3329

OP = {
    "END": 0x00, "HINIT": 0x01, "HABS": 0x02, "HABI": 0x03, "HFIN": 0x04,
    "HSQZ": 0x05, "COPY": 0x06, "CMP": 0x07, "CSEL": 0x08,
    "SAMPLE": 0x10, "CBD": 0x11, "DECODE": 0x12, "ENCODE": 0x13,
    "NTT": 0x20, "INTT": 0x21, "BMUL": 0x22, "ADD": 0x23, "SUB": 0x24,
}

SHA3_256, SHA3_512, SHAKE128, SHAKE256 = 0, 1, 2, 3
CHECK = 0x10  # DECODE: flag coefficients >= q (FIPS 203 7.2 modulus check).
ACC = 0x01    # BMUL: accumulate into destination.

# Data buffer layout (byte offsets in the 8 KB pqc_data_buffer). The
# DATA_IN_ADDR / DATA_OUT_ADDR reset values (pqc_axi_csr parameters in
# pqc_mlkem_top) must equal IN_BASE / OUT_BASE; the host tools use them too.
BUF_SIZE = 0x2000
IN_BASE, IN_SIZE = 0x0000, 0x0E00      # >= 3488 B (Decaps: dk || c).
OUT_BASE, OUT_SIZE = 0x0E00, 0x0E00    # >= 3584 B (KeyGen: ek || dk).
SCR, SCR_SIZE = 0x1C00, 0x0100         # Core scratch.
SEEDS = SCR + 0x000   # G output: rho||sigma or K||r.
HEK = SCR + 0x040     # H(ek).
MPRIME = SCR + 0x060  # m'.
KBAR = SCR + 0x080    # J(z||c).
# Re-encrypted c' (1088 B) does not fit the scratch area; Decaps only
# outputs K (32 B at OUT+0), so c' lives in the otherwise unused output area.
CPRIME_OUT = 0x100

# Poly RAM slots (mlkem_polyram: 8 slots x 128 words, 3-bit slot number).
# S_VEC .. S_VEC+k-1 hold the k-vector (s_hat or r_hat).
S_VEC, S_ACC, S_A, S_E, S_T, S_S = 0, 3, 4, 5, 6, 7
POLY_SLOTS = 8

# level: (k, eta1, eta2, du, dv)
PARAMS = {768: (3, 2, 2, 10, 4)}
LEVEL = 768
PC_BITS = 9                # Width of the program counter in mlkem_ctrl.sv.
OPS = ["keygen", "encaps", "decaps"]


def check_region(a, size, what):
    """Abort if a buffer access leaves its region (IN, OUT or scratch)."""
    off = a & 0x3FFF
    base, lim, name = {1: (0, IN_SIZE, "IN"), 2: (0, OUT_SIZE, "OUT"),
                       0: (SCR, SCR + SCR_SIZE, "scratch")}[a >> 14]
    if not (base <= off and off + size <= lim):
        raise SystemExit("%s: bytes 0x%x..0x%x outside the %s region" % (what, off, off + size - 1, name))


def ABS(o):
    return o & 0x3FFF


def IN(o):
    return (1 << 14) | (o & 0x3FFF)


def OUT(o):
    return (2 << 14) | (o & 0x3FFF)


def astr(a):
    off = a & 0x3FFF
    return {0: "0x%04x" % off, 1: "IN+%d" % off, 2: "OUT+%d" % off}[a >> 14]


class Prog:
    def __init__(self):
        self.code = []
        self.apos = None    # Bytes absorbed since HINIT (None: not absorbing).
        self.spos = None    # Bytes squeezed since HFIN (None: not squeezing).

    # keccak_sponge takes chunks of up to 8 bytes that stay inside a 64-bit
    # lane; mlkem_ctrl absorbs / squeezes buffer data one 32-bit word at a
    # time and HABI bytes as one chunk. Check those constraints statically.
    def _absorb(self, n, word):
        assert self.apos is not None, "absorb outside HINIT..HFIN"
        if word:
            assert self.apos % 4 == 0, "HABS at sponge byte %d, not word aligned" % self.apos
        else:
            assert self.apos % 8 + n <= 8, "HABI bytes cross a lane (sponge byte %d)" % self.apos
        self.apos += n

    def _squeeze(self, n, word):
        assert self.spos is not None, "squeeze before HFIN"
        if word:
            assert self.spos % 4 == 0, "HSQZ at sponge byte %d, not word aligned" % self.spos
        self.spos += n

    def emit(self, op, p=0, a=0, b=0, c=0, ln=0, text=""):
        self.code.append((OP[op], p, a, b, c, ln, text or op))
        # Buffer bytes touched: (tagged address, length) per operand.
        n = len(self.code) - 1
        acc = {"HABS": [(a, ln)], "HSQZ": [(a, ln)], "COPY": [(a, ln), (b, ln)],
               "CMP": [(a, ln), (b, ln)], "CSEL": [(a, ln), (b, ln), (c, ln)],
               "DECODE": [(a, 32 * (p & 0x0F))], "ENCODE": [(b, 32 * p)]}.get(op, [])
        for addr, size in acc:
            check_region(addr, size, "pc %d %s" % (n, text or op))
            # mlkem_ctrl moves these operands one 32-bit word per access.
            if (addr & 3) or (size & 3):
                raise SystemExit("pc %d %s: operand not 32-bit word aligned" % (n, text or op))

    def slots(self, *ss):
        for x in ss:
            assert 0 <= x < POLY_SLOTS, "poly slot %d out of range" % x

    # --- byte-string / hash helpers -------------------------------------
    def hinit(self, mode):
        name = ["SHA3-256", "SHA3-512", "SHAKE128", "SHAKE256"][mode]
        self.emit("HINIT", p=mode, text="HINIT  " + name)
        self.apos, self.spos = 0, None

    def habs(self, a, ln):
        self._absorb(ln, True)
        self.emit("HABS", a=a, ln=ln, text="HABS   %s len=%d" % (astr(a), ln))

    def habi(self, *bs):
        v = bs[0] | ((bs[1] << 8) if len(bs) > 1 else 0)
        self._absorb(len(bs), False)
        self.emit("HABI", p=len(bs), a=v, text="HABI   " + ",".join(map(str, bs)))

    def hfin(self):
        self.emit("HFIN")
        self.apos, self.spos = None, 0

    def hsqz(self, a, ln):
        self._squeeze(ln, True)
        self.emit("HSQZ", a=a, ln=ln, text="HSQZ   %s len=%d" % (astr(a), ln))

    def copy(self, s, d, ln):
        self.emit("COPY", a=s, b=d, ln=ln, text="COPY   %s -> %s len=%d" % (astr(s), astr(d), ln))

    def cmp(self, x, y, ln):
        self.emit("CMP", a=x, b=y, ln=ln, text="CMP    %s, %s len=%d" % (astr(x), astr(y), ln))

    def csel(self, s0, s1, d, ln):
        self.emit("CSEL", a=s0, b=s1, c=d, ln=ln,
                  text="CSEL   flag ? %s : %s -> %s len=%d" % (astr(s1), astr(s0), astr(d), ln))

    # --- polynomial helpers ---------------------------------------------
    def sample(self, dst):
        self.slots(dst)
        assert self.spos == 0, "SAMPLE must start a fresh squeeze"
        self.spos = None    # Consumes an unknown number of bytes.
        self.emit("SAMPLE", c=dst, text="SAMPLE -> s%d" % dst)

    def cbd(self, eta, dst):
        self.slots(dst)
        assert self.spos == 0, "CBD must start a fresh squeeze"
        self.spos = None
        self.emit("CBD", p=eta, c=dst, text="CBD%d   -> s%d" % (eta, dst))

    def decode(self, d, a, dst, check=False):
        self.slots(dst)
        self.emit("DECODE", p=d | (CHECK if check else 0), a=a, c=dst, ln=32 * d,
                  text="DECODE d=%d%s %s -> s%d" % (d, " chk" if check else "", astr(a), dst))

    def encode(self, d, src, a):
        self.slots(src)
        self.emit("ENCODE", p=d, a=src, b=a, text="ENCODE d=%d s%d -> %s" % (d, src, astr(a)))

    def ntt(self, s):
        self.slots(s)
        self.emit("NTT", a=s, text="NTT    s%d" % s)

    def intt(self, s):
        self.slots(s)
        self.emit("INTT", a=s, text="INTT   s%d" % s)

    def bmul(self, x, y, dst, acc):
        self.slots(x, y, dst)
        # mlkem_poly_alu reads C long after A / B: C must not alias them.
        assert dst not in (x, y), "BMUL destination aliases a source"
        self.emit("BMUL", p=ACC if acc else 0, a=x, b=y, c=dst,
                  text="BMUL   s%d * s%d -> s%d%s" % (x, y, dst, " (acc)" if acc else ""))

    def add(self, x, y, dst):
        self.slots(x, y, dst)
        self.emit("ADD", a=x, b=y, c=dst, text="ADD    s%d + s%d -> s%d" % (x, y, dst))

    def sub(self, x, y, dst):
        self.slots(x, y, dst)
        self.emit("SUB", a=x, b=y, c=dst, text="SUB    s%d - s%d -> s%d" % (x, y, dst))

    def prf(self, seed, nonce, eta, dst):
        """dst = SamplePolyCBD_eta(PRF_eta(seed, nonce))."""
        self.hinit(SHAKE256)
        self.habs(seed, 32)
        self.habi(nonce)
        self.hfin()
        self.cbd(eta, dst)


def encrypt(p, level, ek, m, r, dst, check):
    """K-PKE.Encrypt(ek, m, r) -> c at dst (FIPS 203 Algorithm 14)."""
    k, eta1, eta2, du, dv = PARAMS[level]
    assert S_VEC + k <= S_ACC, "k-vector overlaps S_ACC"
    rho = ek + 384 * k
    for j in range(k):
        p.prf(r, j, eta1, S_VEC + j)
        p.ntt(S_VEC + j)
    for i in range(k):
        # u[i] = NTT^-1(sum_j A[j][i] o r_hat[j]) + e1[i], A[j][i] = SampleNTT(rho||i||j)
        for j in range(k):
            p.hinit(SHAKE128)
            p.habs(rho, 32)
            p.habi(i, j)
            p.hfin()
            p.sample(S_A)
            p.bmul(S_A, S_VEC + j, S_ACC, j > 0)
        p.intt(S_ACC)
        p.prf(r, k + i, eta2, S_E)
        p.add(S_ACC, S_E, S_ACC)
        p.encode(du, S_ACC, dst + 32 * du * i)
    # v = NTT^-1(t_hat^T o r_hat) + e2 + Decompress_1(ByteDecode_1(m))
    for i in range(k):
        p.decode(12, ek + 384 * i, S_T, check)
        p.bmul(S_T, S_VEC + i, S_ACC, i > 0)
    p.intt(S_ACC)
    p.prf(r, 2 * k, eta2, S_E)
    p.add(S_ACC, S_E, S_ACC)
    p.decode(1, m, S_E)
    p.add(S_ACC, S_E, S_ACC)
    p.encode(dv, S_ACC, dst + 32 * du * k)


def keygen(p, level):
    """ML-KEM.KeyGen_internal(d, z): IN = d||z, OUT = ek||dk."""
    k, eta1, _, _, _ = PARAMS[level]
    ek_len = 384 * k + 32
    dk = ek_len  # dk offset inside OUT.
    rho, sigma = ABS(SEEDS), ABS(SEEDS + 32)
    # (rho, sigma) = G(d || k)
    p.hinit(SHA3_512)
    p.habs(IN(0), 32)
    p.habi(k)
    p.hfin()
    p.hsqz(ABS(SEEDS), 64)
    # s_hat = NTT(s)
    for j in range(k):
        p.prf(sigma, j, eta1, S_VEC + j)
        p.ntt(S_VEC + j)
    # t_hat[i] = sum_j A[i][j] o s_hat[j] + e_hat[i], A[i][j] = SampleNTT(rho||j||i)
    for i in range(k):
        for j in range(k):
            p.hinit(SHAKE128)
            p.habs(rho, 32)
            p.habi(j, i)
            p.hfin()
            p.sample(S_A)
            p.bmul(S_A, S_VEC + j, S_ACC, j > 0)
        p.prf(sigma, k + i, eta1, S_E)
        p.ntt(S_E)
        p.add(S_ACC, S_E, S_ACC)
        p.encode(12, S_ACC, OUT(384 * i))
    p.copy(rho, OUT(384 * k), 32)
    # dk = ByteEncode12(s_hat) || ek || H(ek) || z
    for j in range(k):
        p.encode(12, S_VEC + j, OUT(dk + 384 * j))
    p.copy(OUT(0), OUT(dk + 384 * k), ek_len)
    p.hinit(SHA3_256)
    p.habs(OUT(0), ek_len)
    p.hfin()
    p.hsqz(OUT(dk + 384 * k + ek_len), 32)
    p.copy(IN(32), OUT(dk + 384 * k + ek_len + 32), 32)
    p.emit("END")


def encaps(p, level):
    """ML-KEM.Encaps_internal(ek, m): IN = ek||m, OUT = c||K."""
    k, _, _, du, dv = PARAMS[level]
    ek_len = 384 * k + 32
    ct_len = 32 * (du * k + dv)
    p.hinit(SHA3_256)
    p.habs(IN(0), ek_len)
    p.hfin()
    p.hsqz(ABS(HEK), 32)
    # (K, r) = G(m || H(ek))
    p.hinit(SHA3_512)
    p.habs(IN(ek_len), 32)
    p.habs(ABS(HEK), 32)
    p.hfin()
    p.hsqz(ABS(SEEDS), 64)
    encrypt(p, level, IN(0), IN(ek_len), ABS(SEEDS + 32), OUT(0), check=True)
    p.copy(ABS(SEEDS), OUT(ct_len), 32)
    p.emit("END")


def decaps(p, level):
    """ML-KEM.Decaps_internal(dk, c): IN = dk||c, OUT = K."""
    k, _, _, du, dv = PARAMS[level]
    ek_len = 384 * k + 32
    dk_len = 768 * k + 96
    ct_len = 32 * (du * k + dv)
    ek = IN(384 * k)
    h = IN(384 * k + ek_len)
    z = IN(384 * k + ek_len + 32)
    c = IN(dk_len)
    # m' = K-PKE.Decrypt(dk_pke, c): w = v' - NTT^-1(s_hat^T o NTT(u'))
    for i in range(k):
        p.decode(du, IN(dk_len + 32 * du * i), S_T)
        p.ntt(S_T)
        p.decode(12, IN(384 * i), S_S)
        p.bmul(S_S, S_T, S_ACC, i > 0)
    p.intt(S_ACC)
    p.decode(dv, IN(dk_len + 32 * du * k), S_T)
    p.sub(S_T, S_ACC, S_ACC)
    p.encode(1, S_ACC, ABS(MPRIME))
    # (K', r') = G(m' || h)
    p.hinit(SHA3_512)
    p.habs(ABS(MPRIME), 32)
    p.habs(h, 32)
    p.hfin()
    p.hsqz(ABS(SEEDS), 64)
    # K_bar = J(z || c)
    p.hinit(SHAKE256)
    p.habs(z, 32)
    p.habs(c, ct_len)
    p.hfin()
    p.hsqz(ABS(KBAR), 32)
    # c' = K-PKE.Encrypt(ek, m', r'); K = (c == c') ? K' : K_bar
    encrypt(p, level, ek, ABS(MPRIME), ABS(SEEDS + 32), OUT(CPRIME_OUT), check=False)
    p.cmp(OUT(CPRIME_OUT), c, ct_len)
    p.csel(ABS(SEEDS), ABS(KBAR), OUT(0), 32)
    p.emit("END")


def brv7(i):
    return int("{:07b}".format(i)[::-1], 2)


def gen_rom(outdir):
    p = Prog()
    entries = []
    for fn in (keygen, encaps, decaps):
        entries.append(len(p.code))
        fn(p, LEVEL)
    n = len(p.code)
    # pc = all-ones is the sequencer's "load entry" sentinel, so it must stay free.
    if n >= (1 << PC_BITS) - 1:
        raise SystemExit("%d instructions do not fit the %d-bit program counter" % (n, PC_BITS))
    lines = []
    w = lines.append
    w("// mlkem_ucode_rom.sv - ML-KEM microcode ROM (GENERATED, do not edit)")
    w("//")
    w("// Generated by scripts/gen_mlkem_ucode.py. %d instructions." % n)
    w("// Programs implement FIPS 203 KeyGen_internal, Encaps_internal and")
    w("// Decaps_internal for ML-KEM-%d; see the generator for the" % LEVEL)
    w("// instruction format and buffer layout. Implemented in LUTs.")
    w("")
    w("/* verilator lint_off DECLFILENAME */")
    w("module mlkem_ucode_rom #(")
    w("    parameter int ADDR_W = %d" % PC_BITS)
    w(") (")
    w("    input  logic              clk,")
    w("    input  logic [ADDR_W-1:0] addr,")
    w("    output logic [95:0]       data,   // Registered: valid 1 cycle after addr.")
    w("    input  logic [1:0]        op,     // 0=KeyGen, 1=Encaps, 2=Decaps.")
    w("    output logic [ADDR_W-1:0] entry   // Program start address.")
    w(");")
    w("")
    w("    always_comb begin")
    w("        case (op)")
    for oi, pc in enumerate(entries):
        w("            2'd%d: entry = ADDR_W'(%d);  // %s" % (oi, pc, OPS[oi]))
    w("            default: entry = '0;")
    w("        endcase")
    w("    end")
    w("")
    w('    (* rom_style = "distributed" *) logic [95:0] rom_q;')
    w("    assign data = rom_q;")
    w("")
    w("    always_ff @(posedge clk) begin")
    w("        case (addr)")
    starts = {pc: oi for oi, pc in enumerate(entries)}
    for pc, (op, pp, a, b, c, ln, text) in enumerate(p.code):
        if pc in starts:
            w("            // ---- %s ML-KEM-%d ----" % (OPS[starts[pc]], LEVEL))
        word = (op << 88) | (pp << 80) | (a << 64) | (b << 48) | (c << 32) | (ln << 16)
        w("            ADDR_W'(%d): rom_q <= 96'h%024x;  // %s" % (pc, word, text))
    w("            default: rom_q <= '0;  // END")
    w("        endcase")
    w("    end")
    w("")
    w("endmodule")
    with open(os.path.join(outdir, "mlkem_ucode_rom.sv"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return n


def gen_zetas(outdir):
    zeta = [pow(17, brv7(i), Q) for i in range(128)]
    gamma = [pow(17, 2 * brv7(i) + 1, Q) for i in range(128)]
    lines = []
    w = lines.append
    w("// mlkem_zetas.sv - ML-KEM NTT constants (GENERATED, do not edit)")
    w("//")
    w("// Generated by scripts/gen_mlkem_ucode.py (FIPS 203 Appendix A):")
    w("//   zeta[i]  = 17^BitRev7(i)       mod 3329   (NTT / NTT^-1)")
    w("//   gamma[i] = 17^(2*BitRev7(i)+1) mod 3329   (MultiplyNTTs)")
    w("")
    w("module mlkem_zetas (")
    w("    input  logic [6:0]  zeta_idx,")
    w("    output logic [11:0] zeta,")
    w("    input  logic [6:0]  gamma_idx,")
    w("    output logic [11:0] gamma")
    w(");")
    w("")
    for name, tab in (("zeta", zeta), ("gamma", gamma)):
        w("    always_comb begin")
        w("        case (%s_idx)" % name)
        for i, v in enumerate(tab):
            w("            7'd%d: %s = 12'd%d;" % (i, name, v))
        w("            default: %s = '0;" % name)
        w("        endcase")
        w("    end")
        w("")
    w("endmodule")
    with open(os.path.join(outdir, "mlkem_zetas.sv"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Generate the ML-KEM microcode and zeta ROMs.")
    ap.add_argument("outdir", nargs="?", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "hdl", "core"))
    ap.add_argument("--levels", type=int, nargs="+", choices=[LEVEL], default=[LEVEL],
                    help="ML-KEM parameter sets to include (only 768 is supported)")
    args = ap.parse_args()
    n = gen_rom(args.outdir)
    gen_zetas(args.outdir)
    print("wrote %d microcode words (ML-KEM-%d) + zeta ROM to %s"
          % (n, LEVEL, os.path.normpath(args.outdir)))


if __name__ == "__main__":
    main()
