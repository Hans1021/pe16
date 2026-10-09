"""PE16 protocol-engine ISA: encoder, assembler and cycle-accurate golden model.

This file is the executable reference for docs/isa.md. The RTL (src/pe_core.v)
and the Lean model (lean/Pe16/Isa.lean) must agree with it cycle for cycle.
"""
import math
import re

M16 = 0xFFFF
CLOCK_HZ = 48_000_000   # design clock; USB low speed needs an integer 32 clocks per bit
FDEPTH = 4

JMP, WAIT, DLY, OUT, IN, SET, MOV, FIFO = range(8)
COND = {"": 0, "always": 0, "x0": 1, "x--": 2, "y0": 3, "y--": 4, "b": 5, "s": 6, "cz": 7, "x!=y": 8, "due": 9, "txe": 10}
OUTM = {"raw": 0, "crc": 1, "stuff": 2, "oe": 3}
INM = {"raw": 0, "crc": 1, "destuff": 2, "peek": 3}
SETD = {"pin": 0, "oe": 1, "x": 2, "y": 3, "ctl": 4, "pins": 5, "oes": 6}
REGS = {"x": 0, "y": 1, "osr": 2, "isr": 3, "crc": 4, "pins": 5, "oe": 6, "null": 7}
FIFOF = {"pull": 0, "push": 1, "halt": 2, "nop": 3}
CTL = {"crc0": 1, "crc1": 2, "stf": 4, "isr0": 8, "ov0": 16, "sync": 32}


def enc(op, rest):
    assert 0 <= rest < (1 << 13)
    return (op << 13) | rest


def clock_helpers(clk):
    """Time-to-cycles helpers for `{...}` expressions. Durations round up so that minimum
    timing requirements are met; `bit(baud)` rounds to the nearest cycle."""
    return {"ns": lambda t: math.ceil(t * clk / 1e9 - 1e-9),
            "us": lambda t: math.ceil(t * clk / 1e6 - 1e-9),
            "ms": lambda t: math.ceil(t * clk / 1e3 - 1e-9),
            "bit": lambda baud: round(clk / baud),
            "max": max, "min": min, "CLK": clk}


def assemble(text, clock_hz=None):
    """Two-pass assembler. Syntax: see docs/isa.md. Returns list of 16-bit words.
    `.equ NAME expr` defines a constant; `{expr}` is evaluated with ns/us/ms/bit helpers."""
    env = clock_helpers(clock_hz or CLOCK_HZ)
    lines, labels = [], {}

    def subst(line):
        return re.sub(r"\{([^}]*)\}", lambda m: str(int(eval(m.group(1), {"__builtins__": {}}, env))), line)

    for raw in text.splitlines():
        s = raw.split(";")[0].strip()
        if not s:
            continue
        if s.startswith(".equ"):
            _, name, expr = s.split(None, 2)
            env[name] = int(eval(subst(expr), {"__builtins__": {}}, env))
            continue
        s = subst(s)
        while ":" in s.split()[0] if s else False:
            lab, s = s.split(":", 1)
            lab = lab.strip().lower()
            if lab in labels:
                raise ValueError(f"duplicate label: {lab}")
            labels[lab] = len(lines)
            s = s.strip()
        if s:
            lines.append(s)

    def num(t):
        t = t.strip()
        return labels[t] if t in labels else int(t, 0)

    words = []
    for s in lines:
        tok = re.split(r"[,\s]+", s.lower().strip())
        m, a = tok[0], tok[1:]
        if m == "jmp":
            c, t = (a[0], a[1]) if len(a) == 2 else ("always", a[0])
            cc = COND[c]
            w = enc(JMP, ((cc & 7) << 10) | ((cc >> 3) << 9) | num(t))
        elif m == "wait":
            w = enc(WAIT, (num(a[0]) << 12) | (num(a[1]) << 9))
        elif m == "dly":
            if a[0] == "x":
                w = enc(DLY, 1 << 12)
            else:
                k = num(a[0])
                if not 1 <= k <= 4095:
                    raise ValueError(f"DLY {k} out of range 1..4095: {s}")
                w = enc(DLY, k)
        elif m == "out":
            w = enc(OUT, (OUTM[a[0]] << 11) | (num(a[1]) << 8))
        elif m == "in":
            w = enc(IN, (INM[a[0]] << 11) | (num(a[1]) << 8))
        elif m == "set":
            d = SETD[a[0]]
            if d in (0, 1):
                v = (num(a[1]) << 7) | (num(a[2]) & 1)
            elif d == 4:
                v = 0
                for f in a[1:]:
                    v |= CTL[f]
            else:
                v = num(a[1])
                if not 0 <= v <= 0x3FF:
                    raise ValueError(f"SET immediate {v} out of range 0..1023: {s}")
            w = enc(SET, (d << 10) | v)
        elif m == "mov":
            src = a[1]
            inv = 1 if src.startswith("~") else 0
            aln = 1 if "^" in src else 0
            rev = 1 if "@" in src else 0
            w = enc(MOV, (REGS[a[0]] << 10) | (REGS[src.strip("~^@")] << 7) | (inv << 6) | (aln << 5) | (rev << 4))
        elif m in FIFOF:
            w = enc(FIFO, FIFOF[m] << 11)
        elif m == "nop":
            w = enc(FIFO, 3 << 11)
        else:
            raise ValueError("bad mnemonic: " + s)
        words.append(w)
    return words


class Cfg:
    def __init__(self, poly=0, crcw=15, osr_lsb=1, isr_lsb=1, stuff_n=0, stuff_ones=0, nrzi=0, diff=0):
        self.poly, self.crcw, self.osr_lsb, self.isr_lsb = poly, crcw, osr_lsb, isr_lsb
        self.stuff_n, self.stuff_ones, self.nrzi, self.diff = stuff_n, stuff_ones, nrzi, diff

    def word(self, reg):
        if reg == 0:
            return self.poly & M16
        if reg == 1:
            return (self.crcw & 15) | (self.osr_lsb << 4) | (self.isr_lsb << 5) | \
                   ((self.stuff_n & 7) << 6) | (self.stuff_ones << 9) | \
                   (self.nrzi << 10) | (self.diff << 11)
        raise KeyError(reg)


class Engine:
    """Cycle-accurate model of one engine. step() = one rising clock edge."""

    def __init__(self, imem, cfg):
        self.imem = list(imem) + [0] * (256 - len(imem))
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.pc = 0
        self.x = self.y = self.osr = self.isr = self.crc = 0
        self.pout = self.poe = 0
        self.b = self.s = self.ov = 0
        self.since = 0
        self.last = 0
        self.run = 0
        self.halt = 0
        self.rxl = 0
        self.txf, self.rxf = [], []

    def state(self):
        return (self.pc, self.x, self.y, self.osr, self.isr, self.crc, self.pout, self.poe,
                self.b, self.s, self.ov, self.since, self.last, self.run, self.halt,
                len(self.txf), len(self.rxf), self.rxl)

    # ---- datapath helpers ----
    def mask(self):
        return (1 << (self.cfg.crcw + 1)) - 1

    def crc_step(self, crc, d):
        w = self.cfg.crcw + 1
        fb = ((crc >> (w - 1)) & 1) ^ d
        crc = (crc << 1) & self.mask()
        return crc ^ (self.cfg.poly & self.mask()) if fb else crc

    def osr_bit(self):
        return self.osr & 1 if self.cfg.osr_lsb else (self.osr >> 15) & 1

    def osr_shift(self, osr):
        return osr >> 1 if self.cfg.osr_lsb else (osr << 1) & M16

    def isr_shift(self, isr, b):
        return (isr >> 1) | (b << 15) if self.cfg.isr_lsb else ((isr << 1) & M16) | b

    def stuff_due(self):
        return self.cfg.stuff_n != 0 and self.run >= self.cfg.stuff_n

    def stuff_update(self, b):
        """Update (last, run) after a data bit b on the line."""
        if self.cfg.stuff_ones:
            return b, (min(self.run + 1, 7) if b else 0)
        return b, (min(self.run + 1, 7) if b == self.last else 1)

    def rd(self, r, pins):
        return [self.x, self.y, self.osr, self.isr, self.crc, pins & 0xFF, self.poe, 0][r]

    # ---- one clock ----
    def step(self, pins, host_push=None, host_pop=False):
        n = dict(pc=self.pc, x=self.x, y=self.y, osr=self.osr, isr=self.isr, crc=self.crc,
                 pout=self.pout, poe=self.poe, b=self.b, s=self.s, ov=self.ov,
                 last=self.last, run=self.run, halt=self.halt, rxl=self.rxl)
        since_n = min(self.since + 1, M16)
        tx_pop = rx_push = None
        ins = self.imem[self.pc]
        op, r = ins >> 13, ins & 0x1FFF
        adv = True
        if self.halt:
            adv = False
        elif op == JMP:
            c, a = ((r >> 10) & 7) | (((r >> 9) & 1) << 3), r & 0xFF
            take = [True, self.x == 0, self.x != 0, self.y == 0, self.y != 0,
                    self.b == 1, self.s == 1, self.crc == 0, self.x != self.y,
                    self.stuff_due(), len(self.txf) == 0][c] if c < 11 else False
            if c == 2:
                n["x"] = (self.x - 1) & M16
            if c == 4:
                n["y"] = (self.y - 1) & M16
            if take:
                n["pc"], adv = a, False
        elif op == WAIT:
            pol, i = (r >> 12) & 1, (r >> 9) & 7
            if ((pins >> i) & 1) != pol:
                adv = False
        elif op == DLY:
            k = self.x if (r >> 12) & 1 else r & 0xFFF
            k = max(k, 1)
            if self.since + 1 >= k:
                since_n = 0
                if self.since + 1 > k:
                    n["ov"] = 1
            else:
                adv = False
        elif op == OUT:
            m, i = (r >> 11) & 3, (r >> 8) & 7
            bit = self.osr_bit()
            if m == 3:
                n["poe"] = (self.poe & ~(1 << i)) | (bit << i)
                n["osr"] = self.osr_shift(self.osr)
                n["b"] = bit
            else:
                if m == 2 and self.stuff_due():
                    v = 0 if self.cfg.stuff_ones else self.last ^ 1
                    n["s"] = 1
                    n["last"], n["run"] = v, (0 if self.cfg.stuff_ones else 1)
                else:
                    v = bit
                    n["osr"] = self.osr_shift(self.osr)
                    n["s"] = 0
                    if m >= 1:
                        n["crc"] = self.crc_step(self.crc, bit)
                    if m == 2:
                        n["last"], n["run"] = self.stuff_update(bit)
                cur = (self.pout >> i) & 1
                line = (cur if v else cur ^ 1) if self.cfg.nrzi else v
                po = (self.pout & ~(1 << i)) | (line << i)
                if self.cfg.diff:
                    j = (i + 1) & 7
                    po = (po & ~(1 << j)) | ((line ^ 1) << j)
                n["pout"] = po
                n["b"] = v
        elif op == IN:
            m, i = (r >> 11) & 3, (r >> 8) & 7
            l = (pins >> i) & 1
            if m == 3:                # peek: raw level, NRZI state untouched
                n["b"] = l
            else:
                bit = (1 if l == self.rxl else 0) if self.cfg.nrzi else l
                n["rxl"] = l
                n["b"] = bit
            if m == 3:
                pass
            elif m == 2 and self.stuff_due():
                n["s"] = 1
                n["last"], n["run"] = bit, (0 if self.cfg.stuff_ones else 1)
            else:
                n["isr"] = self.isr_shift(self.isr, bit)
                n["s"] = 0
                if m >= 1:
                    n["crc"] = self.crc_step(self.crc, bit)
                if m == 2:
                    n["last"], n["run"] = self.stuff_update(bit)
        elif op == SET:
            d, v = (r >> 10) & 7, r & 0x3FF
            i, val = (v >> 7) & 7, v & 1
            if d == 0:
                n["pout"] = (self.pout & ~(1 << i)) | (val << i)
            elif d == 1:
                n["poe"] = (self.poe & ~(1 << i)) | (val << i)
            elif d == 2:
                n["x"] = v
            elif d == 3:
                n["y"] = v
            elif d == 4:
                if v & 1:
                    n["crc"] = 0
                if v & 2:
                    n["crc"] = self.mask()
                if v & 4:
                    n["last"], n["run"] = 0, 0
                if v & 8:
                    n["isr"] = 0
                if v & 16:
                    n["ov"] = 0
                if v & 32:
                    since_n = 0
            elif d == 5:
                n["pout"] = v & 0xFF
            elif d == 6:
                n["poe"] = v & 0xFF
        elif op == MOV:
            d, sr, inv = (r >> 10) & 7, (r >> 7) & 7, (r >> 6) & 1
            val = self.rd(sr, pins)
            if inv:
                val = ~val & M16
            if (r >> 5) & 1:          # align: left-justify a CRC-width value, pad with ones
                sh = 15 - self.cfg.crcw
                val = ((val << sh) | ((1 << sh) - 1)) & M16
            if (r >> 4) & 1:          # bit-reverse 16
                val = int(format(val, "016b")[::-1], 2)
            if d == 0:
                n["x"] = val
            elif d == 1:
                n["y"] = val
            elif d == 2:
                n["osr"] = val
            elif d == 3:
                n["isr"] = val
            elif d == 4:
                n["crc"] = val & self.mask()
            elif d == 5:
                n["pout"] = val & 0xFF
            elif d == 6:
                n["poe"] = val & 0xFF
        elif op == FIFO:
            f = (r >> 11) & 3
            if f == 0:
                if self.txf:
                    tx_pop = True
                    n["osr"] = self.txf[0]
                else:
                    adv = False
            elif f == 1:
                if len(self.rxf) < FDEPTH:
                    rx_push = self.isr
                else:
                    adv = False
            elif f == 2:
                n["halt"], adv = 1, False
        if adv:
            n["pc"] = (self.pc + 1) & 0xFF
        # commit
        for k2, v2 in n.items():
            setattr(self, k2, v2)
        self.since = since_n
        # FIFOs: engine side and host side see start-of-cycle state
        txf, rxf = list(self.txf), list(self.rxf)
        if tx_pop:
            txf.pop(0)
        if host_push is not None and len(self.txf) < FDEPTH:
            txf.append(host_push & M16)
        if host_pop and self.rxf:
            rxf.pop(0)
        if rx_push is not None:
            rxf.append(rx_push)
        self.txf, self.rxf = txf, rxf
