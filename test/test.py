# SPDX-License-Identifier: Apache-2.0
"""Top-level tests: load firmware over the SPI host port, check pins with reference models
written independently of the engine (UART decoder/encoder, SPI target, CAN frame builder
and decoder from ISO 11898-1 rules)."""
import os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "isa"))
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly
import pe16

FW = os.environ.get("PE_FW_DIR", os.path.join(os.path.dirname(__file__), "..", "fw"))
HALF = 4  # SPI half period in system clocks
MISO_DELAY = 5  # SPI target model output delay after SCK falls (clocks)
C = pe16.clock_helpers(pe16.CLOCK_HZ)
CAN_T = C['bit'](500000)


def fw(name):
    return pe16.assemble(open(os.path.join(FW, name + ".pe")).read())


# ---------------- SPI host driver ----------------
class Host:
    def __init__(self, dut):
        self.dut = dut
        self.ui = 0b010  # CS_N high

    def _set(self, sck=None, cs=None, mosi=None):
        if sck is not None:
            self.ui = (self.ui & ~1) | sck
        if cs is not None:
            self.ui = (self.ui & ~2) | (cs << 1)
        if mosi is not None:
            self.ui = (self.ui & ~4) | (mosi << 2)
        self.dut.ui_in.value = self.ui

    async def xfer(self, bits):
        got = []
        for b in bits:
            self._set(mosi=b)
            await ClockCycles(self.dut.clk, HALF)
            self._set(sck=1)
            got.append(int(self.dut.uo_out.value) & 1)
            await ClockCycles(self.dut.clk, HALF)
            self._set(sck=0)
        return got

    async def cmd(self, eng, code, payload_bits=(), nread=0):
        self._set(cs=0, sck=0)
        await ClockCycles(self.dut.clk, HALF)
        c = (eng << 6) | code
        await self.xfer([(c >> (7 - i)) & 1 for i in range(8)])
        got = await self.xfer(list(payload_bits) + [0] * (16 * nread))
        await ClockCycles(self.dut.clk, HALF)
        self._set(cs=1)
        await ClockCycles(self.dut.clk, 3 * HALF)
        rd = got[len(payload_bits):]
        return [int("".join(map(str, rd[16 * i:16 * i + 16])), 2) for i in range(nread)]

    @staticmethod
    def b8(v):
        return [(v >> (7 - i)) & 1 for i in range(8)]

    @staticmethod
    def b16(v):
        return [(v >> (15 - i)) & 1 for i in range(16)]

    async def load(self, eng, words, cfg):
        await self.cmd(eng, 7, self.b8(2))                       # stop + engine reset
        await self.cmd(eng, 1, self.b8(0) + sum((self.b16(w) for w in words), []))
        await self.cmd(eng, 2, self.b8(0) + self.b16(cfg.word(0)))
        await self.cmd(eng, 2, self.b8(1) + self.b16(cfg.word(1)))
        await self.cmd(eng, 7, self.b8(1))                       # run

    async def push(self, eng, words):
        await self.cmd(eng, 3, sum((self.b16(w) for w in words), []))

    async def pop(self, eng, n=1):
        return await self.cmd(eng, 4, nread=n)

    async def counts(self, eng):
        v = (await self.cmd(eng, 6, nread=1))[0]
        return (v >> 3) & 7, v & 7  # rx, tx

    async def status(self, eng):
        v = (await self.cmd(eng, 5, nread=1))[0]
        return dict(pc=v >> 8, run=(v >> 7) & 1, halt=(v >> 6) & 1, ov=(v >> 5) & 1)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0b010
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return Host(dut)


def pin(dut, i):
    return (int(dut.uio_out.value) >> i) & 1


# ---------------- UART ----------------
async def uart_capture(dut, i, period, nbytes, timeout=200000):
    """Independent UART decoder: detect start edge, sample each bit at its centre."""
    out = []
    for _ in range(nbytes):
        n = 0
        while pin(dut, i) == 1:
            await RisingEdge(dut.clk)
            n += 1
            assert n < timeout, "UART: no start bit"
        await ClockCycles(dut.clk, period // 2)
        assert pin(dut, i) == 0, "UART: start bit glitch"
        v = 0
        for k in range(8):
            await ClockCycles(dut.clk, period)
            v |= pin(dut, i) << k
        await ClockCycles(dut.clk, period)
        assert pin(dut, i) == 1, "UART: framing error (stop bit low)"
        out.append(v)
    return out


@cocotb.test()
async def uart_tx(dut):
    h = await setup(dut)
    assert int(dut.uio_oe.value) == 0
    await h.load(0, fw("uart_tx"), pe16.Cfg(osr_lsb=1))
    await ClockCycles(dut.clk, 20)
    assert int(dut.uio_oe.value) & 1 and pin(dut, 0) == 1
    data = [0x55, 0xA3, 0x00, 0xFF]
    cap = cocotb.start_soon(uart_capture(dut, 0, 16, len(data)))
    await h.push(0, data)
    got = await cap
    assert got == data, f"UART TX got {[hex(x) for x in got]}"
    st = await h.status(0)
    assert st["run"] == 1


@cocotb.test()
async def uart_tx_period(dut):
    """Edge timing: every transition in a frame is an exact multiple of 16 clocks after the start edge."""
    h = await setup(dut)
    await h.load(0, fw("uart_tx"), pe16.Cfg(osr_lsb=1))
    await ClockCycles(dut.clk, 20)
    cocotb.start_soon(h.push(0, [0x5A]))
    t, prev, edges = 0, 1, []
    while len(edges) < 1 or t - edges[0] < 16 * 11:
        await RisingEdge(dut.clk)
        await ReadOnly()
        cur = pin(dut, 0)
        if cur != prev:
            edges.append(t)
        prev, t = cur, t + 1
        assert t < 100000
    rel = [e - edges[0] for e in edges]
    assert all(r % 16 == 0 for r in rel), rel
    dut._log.info(f"UART TX edge offsets (clocks): {rel}")


@cocotb.test()
async def uart_rx(dut):
    h = await setup(dut)
    await h.load(0, fw("uart_rx"), pe16.Cfg(isr_lsb=1))
    rng = random.Random(1)
    data = [rng.randrange(256) for _ in range(3)]
    # the transmitter runs 5% slow or 5% fast: a receiver sampling at the bit centre
    # tolerates this, one sampling off-centre by more than a few clocks does not
    data = data + [0x5A, 0xA5, 0x0F, 0xF0]
    for n, v in enumerate(data):
        await ClockCycles(dut.clk, rng.randrange(1, 13))   # random phase vs engine
        fs = [float(x) for x in os.environ.get('UART_F', '0.95,1.05').split(',')]
        f = fs[n % len(fs)]
        bits = [0] + [(v >> k) & 1 for k in range(8)] + [1]
        t = 0
        for k, b in enumerate(bits):
            dut.uio_in.value = 0xFD | (b << 1)
            nxt = round((k + 1) * 16 * f)
            await ClockCycles(dut.clk, nxt - t)
            t = nxt
        await ClockCycles(dut.clk, 16)
        got = await h.pop(0)
        assert got[0] >> 8 == v, f"UART RX {got[0]:04x} vs {v:02x}"
    rx, tx = await h.counts(0)
    assert rx == 0


# ---------------- SPI ----------------
@cocotb.test()
async def spi_controller(dut):
    h = await setup(dut)
    await h.load(0, fw("spi_ctl"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    await ClockCycles(dut.clk, 20)
    assert pin(dut, 5) == 1 and pin(dut, 2) == 0
    rng = random.Random(2)
    tx = [rng.randrange(256) for _ in range(3)]
    reply = [rng.randrange(256) for _ in range(3)]
    seen = []

    async def target():
        """Independent mode-0 SPI target: MISO changes on SCK fall, MOSI sampled on SCK rise."""
        for r in reply:
            while pin(dut, 5) == 1:
                await RisingEdge(dut.clk)
            sh_out, sh_in, nb = r, 0, 0
            dut.uio_in.value = 0xEF | (((sh_out >> 7) & 1) << 4)
            prev = pin(dut, 2)
            while pin(dut, 5) == 0:
                await RisingEdge(dut.clk)
                cur = pin(dut, 2)
                if cur and not prev:
                    sh_in = ((sh_in << 1) | pin(dut, 3)) & 0xFF
                    nb += 1
                if prev and not cur and nb < 8:
                    # a slow target: MISO changes 5 clocks (104 ns) after SCK falls, still
                    # before the next rising edge, where mode 0 requires it to be valid
                    sh_out = (sh_out << 1) & 0xFF
                    await ClockCycles(dut.clk, MISO_DELAY)
                    dut.uio_in.value = 0xEF | (((sh_out >> 7) & 1) << 4)
                prev = cur
            assert nb == 8, f"SPI: {nb} clocks in frame"
            seen.append(sh_in)

    t = cocotb.start_soon(target())
    got = []
    for v in tx:
        await h.push(0, [v << 8])
        await ClockCycles(dut.clk, 400)
        got.append((await h.pop(0))[0] & 0xFF)
    await t
    assert seen == tx, f"target saw {seen} expected {tx}"
    assert got == reply, f"controller got {got} expected {reply}"


# ---------------- CAN ----------------
def can_crc15(bits):
    """ISO 11898-1 CRC-15 (shift-register form from the standard)."""
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_frame(ident, data, ext=False):
    """Reference frame builder. Returns (unstuffed SOF..data bits, full line bits after SOF)."""
    bits = [0]
    if ext:
        base, extid = ident >> 18, ident & 0x3FFFF
        bits += [(base >> (10 - i)) & 1 for i in range(11)] + [1, 1]          # SRR, IDE
        bits += [(extid >> (17 - i)) & 1 for i in range(18)] + [0, 0, 0]      # RTR r1 r0
    else:
        bits += [(ident >> (10 - i)) & 1 for i in range(11)] + [0, 0, 0]     # RTR IDE r0
    bits += [(len(data) >> (3 - i)) & 1 for i in range(4)]
    for d in data:
        bits += [(d >> (7 - i)) & 1 for i in range(8)]
    crc = can_crc15(bits)
    body = bits + [(crc >> (14 - i)) & 1 for i in range(15)]
    stuffed, run, last = [], 0, None
    for b in body:
        stuffed.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            stuffed.append(1 - b)
            last, run = 1 - b, 1
    return bits, stuffed + [1, 1, 1] + [1] * 7   # CRC delim, ACK slot, ACK delim, EOF


def can_destuff_decode(line):
    """Independent receiver: destuff, parse, check CRC. Returns (id, data, ext, crc_ok)."""
    out, run, last, i = [], 0, None, 0
    while True:
        b = line[i]
        i += 1
        if run == 5:
            assert b != last, f"stuff error at line bit {i-1}"
            last, run = b, 1
            continue
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        ext = len(out) > 13 and out[13] == 1
        hdr = 39 if ext else 19
        if len(out) >= hdr:
            dlc = int("".join(map(str, out[hdr - 4:hdr])), 2)
            if len(out) == hdr + 8 * dlc + 15:
                break
    assert line[i] == 1, "CRC delimiter not recessive"
    crc_ok = can_crc15(out) == 0
    ident = int("".join(map(str, out[1:12])), 2)
    if ext:
        ident = (ident << 18) | int("".join(map(str, out[14:32])), 2)
    data = [int("".join(map(str, out[hdr + 8 * k: hdr + 8 * k + 8])), 2) for k in range(dlc)]
    return ident, data, ext, crc_ok


async def sample_line(dut, i, period, nbits):
    while pin(dut, i) == 1:
        await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, period // 2)
    bits = []
    for _ in range(nbits):
        bits.append(pin(dut, i))
        await ClockCycles(dut.clk, period)
    return bits


@cocotb.test()
async def can_tx(dut):
    h = await setup(dut)
    cfg = pe16.Cfg(poly=0x4599, crcw=14, osr_lsb=0, stuff_n=5, stuff_ones=0)
    await h.load(0, fw("can_tx"), cfg)
    await ClockCycles(dut.clk, 20)
    rng = random.Random(3)
    cases = [(0x123, [0x11, 0x22], False), (0x000, [], False), (0x7FF, [0xFF] * 8, False),
             (0x555, [0x00] * 8, False), (0x1ABCDEF0, [0xDE, 0xAD], True), (0x0, [0] * 8, True)]
    cases += [(rng.randrange(2048), [rng.randrange(256) for _ in range(rng.randrange(9))], False)
              for _ in range(6)]
    nstuff = 0
    for ident, data, ext in cases:
        payload, ref_line = can_frame(ident, data, ext)
        nstuff += len(ref_line) - 10 - len(payload) - 15
        words = [len(payload) - 1]
        for k in range(0, len(payload), 16):
            chunk = payload[k:k + 16]
            chunk = chunk + [0] * (16 - len(chunk))
            words.append(int("".join(map(str, chunk)), 2))
        cap = cocotb.start_soon(sample_line(dut, 6, CAN_T, len(ref_line)))
        await h.push(0, words[:4])
        rest = words[4:]
        while rest:  # keep the 4-deep FIFO fed
            _, tx = await h.counts(0)
            if tx < 4:
                await h.push(0, [rest.pop(0)])
        got = await cap
        assert got == ref_line, f"CAN id={ident:x} line mismatch\n got {got}\n ref {ref_line}"
        assert can_destuff_decode(got) == (ident, data, ext, True)
        await ClockCycles(dut.clk, CAN_T * 4)
    st = await h.status(0)
    assert st["ov"] == 0, "bit-timing overrun during CAN frames"
    dut._log.info(f"CAN TX: {len(cases)} frames bit-exact vs reference ({nstuff} stuff bits), ov=0")
