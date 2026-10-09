# SPDX-License-Identifier: Apache-2.0
"""Protocol tests for the remaining firmware. Every peripheral or line model below is
written from the protocol rules and never reads engine state; it only sees pins."""
import os, sys, random
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "isa"))
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge, ReadOnly, Edge
from cocotb.utils import get_sim_time
from test import Host, setup, fw, can_frame, pin, CAN_T
import pe16
C = pe16.clock_helpers(pe16.CLOCK_HZ)
MS = C['ms'](1)


# ---------------------------------------------------------------- pin/bus model
class Bus:
    """Drives uio_in every clock. Open-drain pins are wired-AND with a pull-up; on other pins
    the DUT wins when its output enable is set, else the external driver (default high)."""

    def __init__(self, dut, od=()):
        self.dut, self.od, self.ext = dut, set(od), [None] * 8
        cocotb.start_soon(self._run())

    def value(self):
        out, oe = int(self.dut.uio_out.value), int(self.dut.uio_oe.value)
        v = 0
        for i in range(8):
            d = (out >> i) & 1 if (oe >> i) & 1 else None
            e = self.ext[i]
            if i in self.od:
                b = 0 if (d == 0 or e == 0) else 1
            else:
                b = d if d is not None else (1 if e is None else e)
            v |= b << i
        return v

    def lv(self, i):
        return (self.value() >> i) & 1

    async def _run(self):
        while True:
            await FallingEdge(self.dut.clk)
            self.dut.uio_in.value = self.value()


def watch_bus(dut, bus, i):
    """Background recorder of (clock, level) changes of bus line i."""
    rec = []

    async def run():
        t, prev = 0, bus.lv(i)
        while True:
            await FallingEdge(dut.clk)
            v = bus.lv(i)
            if v != prev:
                rec.append((t, v))
                prev = v
            t += 1
    cocotb.start_soon(run())
    return rec


def widths(rec, level):
    return [b[0] - a[0] for a, b in zip(rec, rec[1:]) if a[1] == level]


async def edges(dut, i, n):
    """Record (time, level) of every change on uio_out[i] for n clocks."""
    ev, prev = [], pin(dut, i)
    for t in range(n):
        await RisingEdge(dut.clk)
        await ReadOnly()
        cur = pin(dut, i)
        if cur != prev:
            ev.append((t, cur))
            prev = cur
    return ev


def bits_lsb(v, n):
    return [(v >> k) & 1 for k in range(n)]


def bits_msb(v, n):
    return [(v >> (n - 1 - k)) & 1 for k in range(n)]


def pack_words(bits, lsb_first):
    words = []
    for k in range(0, len(bits), 16):
        c = bits[k:k + 16] + [0] * (16 - len(bits[k:k + 16]))
        words.append(sum(b << (j if lsb_first else 15 - j) for j, b in enumerate(c)))
    return words


async def stream(dut, h, words, nrx, limit):
    """Host loop: keep the TX FIFO topped up and drain the RX FIFO in the same SPI stream,
    until nrx words have been read or `limit` clocks have passed."""
    got, rest = [], list(words)
    t0 = get_sim_time("ns")
    while len(got) < nrx and get_sim_time("ns") - t0 < limit * 10:
        rx, tx = await h.counts(0)
        if rest and tx < 4:
            k = min(4 - tx, len(rest))
            await h.push(0, rest[:k])
            rest = rest[k:]
        if rx:
            got += await h.pop(0, rx)
    return got


async def feed(h, words):
    """Push words, keeping the 4-deep TX FIFO topped up."""
    await h.push(0, words[:4])
    rest = list(words[4:])
    while rest:
        _, tx = await h.counts(0)
        if tx < 4:
            await h.push(0, [rest.pop(0)])


# ---------------------------------------------------------------- I2C
async def i2c_target_model(dut, bus, addr, rd_data, log, stretch=0):
    SCL, SDA = 0, 1
    pscl, psda = 1, 1
    st, byte, n, rw, hold, cur, nb, mack = "idle", 0, 0, 0, 0, 0, 0, 0
    rq = list(rd_data)
    while True:
        await FallingEdge(dut.clk)
        v = bus.value()
        scl, sda = v & 1, (v >> 1) & 1
        if hold:
            hold -= 1
            if not hold:
                bus.ext[SCL] = None
        if scl and pscl and psda and not sda:
            st, byte, n = "addr", 0, 0
            bus.ext[SDA] = None
            log.append("S")
        elif scl and pscl and not psda and sda:
            st = "idle"
            bus.ext[SDA] = None
            log.append("P")
        elif scl and not pscl:
            if st in ("addr", "w"):
                byte, n = (byte << 1) | sda, n + 1
            elif st == "r_ack":
                mack = sda
                log.append(("mack", sda))
        elif not scl and pscl:
            if st == "addr" and n == 8:
                if byte >> 1 == addr:
                    rw = byte & 1
                    bus.ext[SDA] = 0
                    st = "a_ack"
                    log.append(("addr", byte))
                    if stretch:
                        bus.ext[SCL], hold = 0, stretch
                else:
                    st = "idle"
                    log.append(("nak_addr", byte))
            elif st == "a_ack" or (st == "r_ack" and mack == 0):
                if rw:
                    cur, nb, st = rq.pop(0), 1, "r"
                    bus.ext[SDA] = None if (cur >> 7) & 1 else 0
                else:
                    bus.ext[SDA], st, byte, n = None, "w", 0, 0
            elif st == "r_ack":
                st = "idle"
            elif st == "r":
                if nb < 8:
                    bus.ext[SDA] = None if (cur >> (7 - nb)) & 1 else 0
                    nb += 1
                else:
                    bus.ext[SDA], st = None, "r_ack"
            elif st == "w" and n == 8:
                bus.ext[SDA], st = 0, "w_ack"
                log.append(("w", byte))
            elif st == "w_ack":
                bus.ext[SDA], st, byte, n = None, "w", 0, 0
        pscl, psda = scl, sda


S_, P_, R_, A_ = 0x8000, 0x4000, 0x2000, 0x1000


@cocotb.test()
async def i2c_controller(dut):
    h = await setup(dut)
    bus = Bus(dut, od=(0, 1))
    log = []
    cocotb.start_soon(i2c_target_model(dut, bus, 0x42, [0x9C, 0x3E], log, stretch=300))
    await h.load(0, fw("i2c_ctl"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    await ClockCycles(dut.clk, 50)
    scl = []

    async def watch():
        t, prev = 0, 1
        while True:
            await FallingEdge(dut.clk)
            v = bus.lv(0)
            if v != prev:
                scl.append((t, v))
                prev = v
            t += 1
    cocotb.start_soon(watch())
    # write 0x5A to 0x42; read two bytes (ACK then NACK); address a missing device
    q = C['ns'](500) / 8
    await h.push(0, [S_ | 0x84, P_ | 0x5A])
    await ClockCycles(dut.clk, int(1500 * q))
    await h.push(0, [S_ | 0x85, R_, R_ | A_ | P_])
    await ClockCycles(dut.clk, int(2500 * q))
    await h.push(0, [S_ | P_ | 0xA0])
    got = await stream(dut, h, [], 6, int(20000 * q))
    assert len(got) == 6, f"only {len(got)} result words"
    dut._log.info(f"I2C pops {[hex(x) for x in got]} log {log}")
    assert [g & 1 for g in got[:2]] == [0, 0], "write: expected ACKs"
    assert got[2] & 1 == 0 and got[3] & 0xFF == 0x9C and got[4] & 0xFF == 0x3E
    assert got[5] & 1 == 1, "missing device must NACK"
    assert ("w", 0x5A) in log and ("mack", 0) in log and ("mack", 1) in log
    assert log.count("S") == 3 and log.count("P") == 3
    # SCL timing against I2C fast mode (400 kHz): low >= 1.3 us, high >= 0.6 us
    us = C['us'](1)
    lows = [b[0] - a[0] for a, b in zip(scl, scl[1:]) if a[1] == 0]
    highs = [b[0] - a[0] for a, b in zip(scl, scl[1:]) if a[1] == 1]
    per = [c[0] - a[0] for a, c in zip(scl, scl[2:]) if a[1] == 0]
    dut._log.info(f"I2C SCL: min low {min(lows) / us:.2f} us, min high {min(highs) / us:.2f} us, "
                  f"max freq {1e3 / (min(per) / us):.0f} kHz")
    assert min(lows) >= 1.3 * us and min(highs) >= 0.6 * us and min(per) >= 2.5 * us


async def i2c_bb(dut, bus, T, ops):
    """Bit-banged I2C controller used to test the engine as a target."""
    SCL, SDA = 0, 1
    res = []

    async def w(n=1):
        await ClockCycles(dut.clk, n * T)

    async def clk_bit(drive):
        bus.ext[SDA] = None if drive else 0
        await w()
        bus.ext[SCL] = None
        await ClockCycles(dut.clk, T // 2)
        b = bus.lv(SDA)
        await ClockCycles(dut.clk, T // 2)
        bus.ext[SCL] = 0
        return b

    for op, val in ops:
        if op == "S":
            bus.ext[SDA] = None; await w(); bus.ext[SCL] = None; await w()
            bus.ext[SDA] = 0; await w(); bus.ext[SCL] = 0; await w()
        elif op == "P":
            bus.ext[SDA] = 0; await w(); bus.ext[SCL] = None; await w()
            bus.ext[SDA] = None; await w(2)
        elif op == "W":
            for k in range(8):
                await clk_bit((val >> (7 - k)) & 1)
            res.append(("ack", await clk_bit(1)))
        elif op == "R":
            v = 0
            for k in range(8):
                v = (v << 1) | await clk_bit(1)
            await clk_bit(val)          # val: 1 = NACK
            bus.ext[SDA] = None
            res.append(("rd", v))
    return res


@cocotb.test()
async def i2c_target(dut):
    h = await setup(dut)
    bus = Bus(dut, od=(0, 1))
    await h.load(0, fw("i2c_tgt"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    await h.push(0, [0xC5 << 8])
    await ClockCycles(dut.clk, 50)
    r = await i2c_bb(dut, bus, 40, [("S", 0), ("W", 0x84), ("W", 0x37), ("P", 0)])
    r += await i2c_bb(dut, bus, 40, [("S", 0), ("W", 0x85), ("R", 1), ("P", 0)])
    r += await i2c_bb(dut, bus, 40, [("S", 0), ("W", 0x90), ("P", 0)])
    got = await h.pop(0)
    dut._log.info(f"I2C target: {r}, pushed {got[0]:#x}")
    assert r == [("ack", 0), ("ack", 0), ("ack", 0), ("rd", 0xC5), ("ack", 1)]
    assert got[0] & 0xFF == 0x37


# ---------------------------------------------------------------- SPI target
@cocotb.test()
async def spi_target(dut):
    h = await setup(dut)
    bus = Bus(dut)
    SCK, MOSI, MISO, CS = 2, 3, 4, 5
    bus.ext[SCK], bus.ext[CS] = 0, 1
    await h.load(0, fw("spi_tgt"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    rng = random.Random(7)
    for _ in range(3):
        tx, reply = rng.randrange(256), rng.randrange(256)
        await h.push(0, [reply << 8])
        await ClockCycles(dut.clk, 20)
        bus.ext[CS] = 0
        await ClockCycles(dut.clk, 16)
        got = 0
        for k in range(8):
            bus.ext[MOSI] = (tx >> (7 - k)) & 1
            await ClockCycles(dut.clk, 16)
            bus.ext[SCK] = 1
            await ClockCycles(dut.clk, 1)
            got = (got << 1) | bus.lv(MISO)
            await ClockCycles(dut.clk, 15)
            bus.ext[SCK] = 0
        await ClockCycles(dut.clk, 16)
        bus.ext[CS] = 1
        await ClockCycles(dut.clk, 20)
        rx = (await h.pop(0))[0] & 0xFF
        assert got == reply and rx == tx, f"MISO {got:#x}/{reply:#x} MOSI {rx:#x}/{tx:#x}"


# ---------------------------------------------------------------- JTAG
IDCODE = 0x4BA00477


async def tap_model(dut, bus, log):
    """IEEE 1149.1 TAP: 4-bit IR (IDCODE=0x1, BYPASS=0xF), IDCODE selected after reset."""
    TCK, TMS, TDI, TDO = 0, 1, 2, 3
    nxt = {"TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDR"), "SDR": ("CDR", "SIR"),
           "CDR": ("ShDR", "E1DR"), "ShDR": ("ShDR", "E1DR"), "E1DR": ("PDR", "UDR"),
           "PDR": ("PDR", "E2DR"), "E2DR": ("ShDR", "UDR"), "UDR": ("RTI", "SDR"),
           "SIR": ("CIR", "TLR"), "CIR": ("ShIR", "E1IR"), "ShIR": ("ShIR", "E1IR"),
           "E1IR": ("PIR", "UIR"), "PIR": ("PIR", "E2IR"), "E2IR": ("ShIR", "UIR"),
           "UIR": ("RTI", "SDR")}
    state, ir, sh_ir, dr, dlen, prev = "TLR", 1, 0, 0, 32, 0
    while True:
        await FallingEdge(dut.clk)
        v = bus.value()
        tck, tms, tdi = v & 1, (v >> 1) & 1, (v >> 2) & 1
        if tck and not prev:                       # rising: sample TMS/TDI
            if state == "ShDR":
                dr = (dr >> 1) | (tdi << (dlen - 1))
            if state == "ShIR":
                sh_ir = (sh_ir >> 1) | (tdi << 3)
            if state == "CDR":
                pass
            state = nxt[state][tms]
            if state == "CDR":
                dr, dlen = (IDCODE, 32) if ir == 1 else (0, 1)
            if state == "CIR":
                sh_ir = 0b0001
            if state == "UIR":
                ir = sh_ir
            if state == "TLR":
                ir = 1
            log.append(state)
        if not tck and prev:                       # falling: update TDO
            bus.ext[TDO] = dr & 1 if state == "ShDR" else (sh_ir & 1 if state == "ShIR" else 1)
        prev = tck


@cocotb.test()
async def jtag_idcode(dut):
    h = await setup(dut)
    bus = Bus(dut)
    log = []
    cocotb.start_soon(tap_model(dut, bus, log))
    await h.load(0, fw("jtag"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    # reset, RTI, Select-DR, Capture-DR, Shift-DR x32 (last with TMS=1), Update-DR, RTI
    tms = [1] * 5 + [0, 1, 0, 0] + [0] * 31 + [1, 1, 0]
    tdi = [0] * len(tms)
    pairs = [b for t, d in zip(tms, tdi) for b in (t, d)]
    nw = (len(tms) + 7) // 8
    words = await stream(dut, h, [len(tms) - 1] + pack_words(pairs, False), nw, 40000)
    tdo = []
    for k, wv in enumerate(words):
        nbit = 8 if k < nw - 1 else len(tms) - 8 * (nw - 1)
        tdo += bits_msb(wv & ((1 << nbit) - 1), nbit)
    shifted = tdo[9:41]
    code = sum(b << k for k, b in enumerate(shifted))
    dut._log.info(f"JTAG IDCODE read {code:#010x}, final TAP state {log[-1]}")
    assert code == IDCODE and log[-1] == "RTI" and len(tdo) == len(tms)


# ---------------------------------------------------------------- SWD
DPIDR = 0x2BA01477


async def swd_target(dut, bus, log):
    """SWD target: line reset (>=50 ones), then an 8-bit request; answers DPIDR reads."""
    CLK, IO = 0, 1
    prev, ones, armed, st, n, req, out = 0, 0, False, "idle", 0, 0, []
    while True:
        await FallingEdge(dut.clk)
        v = bus.value()
        clk, io = v & 1, (v >> 1) & 1
        if clk and not prev:
            if st == "idle":
                ones = ones + 1 if io else 0
                if ones >= 50:
                    armed = True
                if armed and io and ones == 1 and n == 0:
                    st, req, n = "req", 1, 1
                elif st == "idle" and ones == 0 and armed:
                    pass
            elif st == "req":
                req |= io << n
                n += 1
                if n == 8:
                    ok = (req & 1) and not (req >> 6) & 1 and (req >> 7) & 1 and \
                        bin(req & 0x1E).count("1") % 2 == (req >> 5) & 1
                    log.append(("req", req, bool(ok)))
                    data = DPIDR
                    par = bin(data).count("1") & 1
                    out = [1, 0, 0] + bits_lsb(data, 32) + [par]
                    st = "trn"
            elif st == "trn":
                bus.ext[IO] = out.pop(0)
                st = "data"
            elif st == "data":
                if out:
                    bus.ext[IO] = out.pop(0)
                else:
                    bus.ext[IO] = None
                    st, n, ones = "idle", 0, 0
        prev = clk


@cocotb.test()
async def swd_dpidr(dut):
    h = await setup(dut)
    bus = Bus(dut)
    log = []
    cocotb.start_soon(swd_target(dut, bus, log))
    await h.load(0, fw("swd"), pe16.Cfg(osr_lsb=1, isr_lsb=1))
    await h.push(0, [55, 0xA5])
    await ClockCycles(dut.clk, 16 * 120)
    w = await h.pop(0, 3)
    stream = bits_lsb(w[0], 16) + bits_lsb(w[1], 16) + bits_lsb(w[2] >> 12, 4)
    ack = stream[:3]
    data = sum(b << k for k, b in enumerate(stream[3:35]))
    par = stream[35]
    dut._log.info(f"SWD: request log {log}, ACK {ack}, DPIDR {data:#010x}, parity {par}")
    assert log and log[0][2], "target rejected the request"
    assert ack == [1, 0, 0] and data == DPIDR and par == bin(DPIDR).count("1") & 1


# ---------------------------------------------------------------- PS/2
@cocotb.test()
async def ps2_receive(dut):
    h = await setup(dut)
    bus = Bus(dut, od=(0, 1))
    await h.load(0, fw("ps2_rx"), pe16.Cfg(isr_lsb=1))
    await ClockCycles(dut.clk, 50)
    half = 1200                                   # 16.7 kHz at 40 MHz
    for code in (0x1C, 0xF0):
        par = 1 - (bin(code).count("1") & 1)      # odd parity
        frame = [0] + bits_lsb(code, 8) + [par, 1]
        for b in frame:                            # device: data changes while CLK high
            bus.ext[1] = 0 if b == 0 else None
            await ClockCycles(dut.clk, half // 2)
            bus.ext[0] = 0
            await ClockCycles(dut.clk, half)
            bus.ext[0] = None
            await ClockCycles(dut.clk, half // 2)
        bus.ext[1] = None
        await ClockCycles(dut.clk, 200)
        w = (await h.pop(0))[0] >> 5
        got = [(w >> k) & 1 for k in range(11)]
        assert got == frame, f"PS/2 {got} vs {frame}"


# ---------------------------------------------------------------- 1-Wire
async def onewire_slave(dut, bus, rom, log, present=True):
    US = C['us'](1)
    DQ = 0
    prev, low_t, t, mode, rxb, nrx, tx, hold, sample_at = 1, 0, 0, "cmd", 0, 0, [], 0, None
    while True:
        await FallingEdge(dut.clk)
        t += 1
        if hold:
            hold -= 1
            if not hold:
                bus.ext[DQ] = None
        lvl = bus.lv(DQ)
        if prev and not lvl and not hold:          # host starts a slot or reset
            low_t = t
            if mode == "tx" and tx and present:
                b = tx.pop(0)
                if b == 0:
                    bus.ext[DQ], hold = 0, 30 * US
            elif mode == "cmd":
                sample_at = t + 30 * US
        if not prev and lvl and low_t:
            dur = t - low_t
            if dur >= 430 * US:                    # reset pulse
                log.append(("reset", dur // US))
                mode, rxb, nrx, sample_at = "cmd", 0, 0, None
                if present:
                    await ClockCycles(dut.clk, 30 * US)
                    t += 30 * US
                    bus.ext[DQ], hold = 0, 120 * US
            low_t = 0
        if sample_at is not None and t == sample_at and mode == "cmd":
            rxb |= bus.lv(DQ) << nrx
            nrx += 1
            sample_at = None
            if nrx == 8:
                log.append(("cmd", rxb))
                if rxb == 0x33:
                    mode = "tx"
                    tx = [b for byte in rom for b in bits_lsb(byte, 8)]
        prev = bus.lv(DQ)


@cocotb.test()
async def onewire(dut):
    h = await setup(dut)
    bus = Bus(dut, od=(0,))
    log = []
    rom = [0x28, 0xB6, 0x42]
    cocotb.start_soon(onewire_slave(dut, bus, rom, log))
    await h.load(0, fw("onewire"), pe16.Cfg(osr_lsb=1, isr_lsb=1))
    dq = watch_bus(dut, bus, 0)
    await h.push(0, [0, (0x33 << 2) | 1, 2, 2])
    await ClockCycles(dut.clk, C['us'](1) * (960 + 3 * 8 * 75) + 2000)
    w = await h.pop(0, 3)
    dut._log.info(f"1-Wire: log {log}, pushes {[hex(x) for x in w]}")
    assert (w[0] >> 15) & 1 == 0, "no presence pulse seen"
    assert ("cmd", 0x33) in log
    assert [(x >> 8) & 0xFF for x in w[1:]] == rom[:2]
    # host-driven slot timing (standard speed). Lows include slave-held zeros during reads.
    us = C['us'](1)
    lows = widths(dq, 0)
    highs = widths(dq, 1)
    lows_us = sorted(set(round(x / us, 1) for x in lows))
    dut._log.info(f"1-Wire low pulses (us): {lows_us}; min recovery {min(highs) / us:.1f} us")
    assert max(lows) >= 480 * us                         # reset
    host = [x for x in lows if not 29 * us <= x <= 31 * us]   # drop slave-held 30 us zeros
    assert all(1 * us <= x <= 15 * us or 60 * us <= x <= 120 * us or 480 * us <= x <= 960 * us
               for x in host), sorted(set(round(x / us, 2) for x in host))
    assert min(highs) >= 1 * us                          # recovery between slots


# ---------------------------------------------------------------- WS2812
@cocotb.test()
async def ws2812(dut):
    h = await setup(dut)
    await h.load(0, fw("ws2812"), pe16.Cfg(osr_lsb=0))
    await ClockCycles(dut.clk, 20)
    grb = [0x10, 0xFF, 0x00, 0xA5, 0x3C, 0x81]           # two LEDs
    bits = [b for byte in grb for b in bits_msb(byte, 8)]
    H0, H1, P = C['ns'](400), C['ns'](800), C['ns'](1250)
    cap = cocotb.start_soon(edges(dut, 0, P * 60 + 1000))
    await h.push(0, pack_words(bits, False))
    ev = await cap
    rises = [t for t, l in ev if l == 1]
    falls = [t for t, l in ev if l == 0]
    highs = [f - r for r, f in zip(rises, falls)]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    dec = [1 if x > (H0 + H1) // 2 else 0 for x in highs]
    dut._log.info(f"WS2812: high widths {sorted(set(highs))}, periods {sorted(set(periods))}")
    assert dec == bits and set(highs) <= {H0, H1} and set(periods) == {P}
    assert pin(dut, 0) == 0


# ---------------------------------------------------------------- DMX512 / LIN
def uart_decode(ev, start, period, nbytes, nstop):
    """Decode 8N<nstop> bytes from an edge list starting at the falling edge `start`."""
    def level(t):
        l = 1
        for te, le in ev:
            if te > t:
                break
            l = le
        return l
    out, t = [], start
    for _ in range(nbytes):
        assert level(t + period // 2) == 0, "start bit"
        v = sum(level(t + period // 2 + period * (k + 1)) << k for k in range(8))
        for s in range(nstop):
            assert level(t + period // 2 + period * (9 + s)) == 1, "stop bit"
        out.append(v)
        nxt = [te for te, le in ev if le == 0 and te > t + period * (8 + nstop)]
        t = nxt[0] if nxt else None
        if t is None:
            break
    return out


@cocotb.test()
async def dmx512(dut):
    h = await setup(dut)
    await h.load(0, fw("dmx"), pe16.Cfg(osr_lsb=1))
    await ClockCycles(dut.clk, 20)
    slots = [0x00, 0x12, 0xFF, 0x80, 0x55]
    T = C['bit'](250000)
    cap = cocotb.start_soon(edges(dut, 0, C['us'](102) + 11 * T * len(slots) + 3000))
    await feed(h, [len(slots) - 1] + slots)
    ev = await cap
    brk = ev[1][0] - ev[0][0]
    mab = ev[2][0] - ev[1][0]
    got = uart_decode(ev, ev[2][0], T, len(slots), 2)
    us = C['us'](1)
    dut._log.info(f"DMX: break {brk / us:.2f} us, MAB {mab / us:.2f} us, slots {got}")
    assert brk >= 88 * us and mab >= 8 * us and got == slots


def lin_pid(i):
    p0 = (i ^ (i >> 1) ^ (i >> 2) ^ (i >> 4)) & 1
    p1 = 1 ^ ((i >> 1) ^ (i >> 3) ^ (i >> 4) ^ (i >> 5)) & 1
    return i | (p0 << 6) | (p1 << 7)


def lin_checksum(bytes_):
    s = 0
    for b in bytes_:
        s += b
        if s > 255:
            s -= 255
    return (~s) & 0xFF


@cocotb.test()
async def lin(dut):
    h = await setup(dut)
    await h.load(0, fw("lin"), pe16.Cfg(osr_lsb=1))
    await ClockCycles(dut.clk, 20)
    pid, data = lin_pid(0x10), [0x12, 0x34]
    frame = [0x55, pid] + data + [lin_checksum([pid] + data)]
    bit = C['bit'](19200)
    cap = cocotb.start_soon(edges(dut, 0, bit * (14 + 10 * len(frame)) + 5000))
    await feed(h, [len(frame) - 1] + frame)
    ev = await cap
    brk = ev[1][0] - ev[0][0]
    delim = ev[2][0] - ev[1][0]
    got = uart_decode(ev, ev[2][0], bit, len(frame), 1)
    ok_pid = lin_pid(got[1] & 0x3F) == got[1]
    ok_sum = lin_checksum(got[1:-1]) == got[-1]
    dut._log.info(f"LIN: break {brk / bit:.2f} bits, delimiter {delim / bit:.2f} bits, bytes {[hex(x) for x in got]}")
    assert brk >= 13 * bit and delim >= bit and got == frame and got[0] == 0x55 and ok_pid and ok_sum


# ---------------------------------------------------------------- MDIO
async def phy_model(dut, bus, regs, log):
    """Clause 22 PHY at address 3: samples MDIO on MDC rising, drives read data after it."""
    MDC, MDIO = 0, 1
    prev, sh, n, st, out = 0, [], 0, "pre", []
    while True:
        await FallingEdge(dut.clk)
        v = bus.value()
        mdc, io = v & 1, (v >> 1) & 1
        if mdc and not prev:
            if st == "pre":
                sh.append(io)
                if len(sh) >= 34 and sh[-34:-2] == [1] * 32 and sh[-2:] == [0, 1]:
                    st, sh = "hdr", []
            elif st == "hdr":
                sh.append(io)
                if len(sh) == 12:
                    op = sh[0] * 2 + sh[1]
                    phy = int("".join(map(str, sh[2:7])), 2)
                    reg = int("".join(map(str, sh[7:12])), 2)
                    if op == 2 and phy == 3:
                        st, out = "rta", [0] + bits_msb(regs.get(reg, 0xFFFF), 16)
                        log.append(("rd", reg))
                    else:
                        st, sh = "wta", []
                        wreg, wphy = reg, phy
            elif st == "rta":
                st = "rdat"
                bus.ext[MDIO] = out.pop(0)
            elif st == "rdat":
                if out:
                    bus.ext[MDIO] = out.pop(0)
                else:
                    bus.ext[MDIO], st, sh = None, "pre", []
            elif st == "wta":
                sh.append(io)
                if len(sh) == 18:
                    assert sh[:2] == [1, 0], "write TA must be 10"
                    val = int("".join(map(str, sh[2:])), 2)
                    if wphy == 3:
                        regs[wreg] = val
                    log.append(("wr", wreg, val))
                    st, sh = "pre", []
        prev = mdc


def mdio_hdr(op, phy, reg):
    return (0b01 << 14) | (op << 12) | (phy << 7) | (reg << 2) | (0b10 if op == 1 else 0)


@cocotb.test()
async def mdio(dut):
    h = await setup(dut)
    bus = Bus(dut)
    regs, log = {2: 0x0141, 3: 0x0DD1}, []
    cocotb.start_soon(phy_model(dut, bus, regs, log))
    await h.load(0, fw("mdio"), pe16.Cfg(osr_lsb=0, isr_lsb=0))
    mdc = watch_bus(dut, bus, 0)
    tb = 2 * C['ns'](200)
    await h.push(0, [mdio_hdr(1, 3, 5), 0xBEEF])
    await ClockCycles(dut.clk, tb * 70)
    await h.push(0, [mdio_hdr(2, 3, 5)])
    await ClockCycles(dut.clk, tb * 70)
    await h.push(0, [mdio_hdr(2, 3, 2)])
    await ClockCycles(dut.clk, tb * 70)
    got = await h.pop(0, 2)
    dut._log.info(f"MDIO: log {log}, reads {[hex(x) for x in got]}")
    assert ("wr", 5, 0xBEEF) in log and got == [0xBEEF, 0x0141]
    ns = pe16.CLOCK_HZ / 1e9
    lo, hi = min(widths(mdc, 0)), min(widths(mdc, 1))
    per = min(b[0] - a[0] for a, b in zip(mdc, mdc[2:]) if a[1] == 1)
    dut._log.info(f"MDC: min high {hi / ns:.0f} ns, min low {lo / ns:.0f} ns, min period {per / ns:.0f} ns")
    assert hi >= 160 * ns and lo >= 160 * ns and per >= 400 * ns


# ---------------------------------------------------------------- CAN receive
@cocotb.test()
async def can_rx(dut):
    h = await setup(dut)
    cfg = pe16.Cfg(poly=0x4599, crcw=14, isr_lsb=0, stuff_n=5, stuff_ones=0)
    await h.load(0, fw("can_rx"), cfg)
    await ClockCycles(dut.clk, 40)
    cases = [(0x123, [0x11, 0x22], False, False), (0x7FF, [0xFF] * 8, False, False),
             (0x000, [], False, False), (0x1ABCDEF0, [0xDE, 0xAD, 0xBE], True, False),
             (0x555, [0x00] * 8, False, False), (0x321, [0x42], False, True)]
    for ident, data, ext, corrupt in cases:
        payload, line = can_frame(ident, data, ext)
        if corrupt:                                   # flip one data bit, keep the old CRC
            bad = list(payload)
            bad[-3] ^= 1
            crc_bits = [line_b for line_b in []]
            from test import can_crc15
            crc = can_crc15(payload)
            body = bad + bits_msb(crc, 15)
            st_, run, last = [], 0, None
            for b in body:
                st_.append(b)
                run = run + 1 if b == last else 1
                last = b
                if run == 5:
                    st_.append(1 - b)
                    last, run = 1 - b, 1
            line = st_ + [1] * 10
        ack_seen = []
        nexp = (4 if ext else 2) + len(data) + 1
        drain = cocotb.start_soon(stream(dut, h, [], nexp, CAN_T * (len(line) + 60)))
        # default run: two frames at +-0.3% bit-rate error (measured tolerance +-0.4%)
        f = float(os.environ.get("CAN_F", str([1.0, 1.0, 0.997, 1.003, 1.0, 1.0][cases.index((ident, data, ext, corrupt)) % 6])))
        t = 0
        for k, b in enumerate(line + [1] * 3):
            nxt = round((k + 1) * CAN_T * f)
            for c in range(nxt - t):
                txd = pin(dut, 6)
                dut.uio_in.value = ((b & txd) << 7) | 0x7F
                if k == len(line) - 9 and c == (nxt - t) // 2:
                    ack_seen.append(txd)
                await RisingEdge(dut.clk)
            t = nxt
        w = await drain
        assert len(w) == nexp, f"got {len(w)} words, expected {nexp}"
        hdr = [(w[0] >> (13 - j)) & 1 for j in range(14)]
        k = 1
        ident_got = int("".join(map(str, hdr[1:12])), 2)
        if hdr[13]:
            e = [(w[1] >> (15 - j)) & 1 for j in range(16)] + [(w[2] >> (4 - j)) & 1 for j in range(5)]
            ident_got = (ident_got << 18) | int("".join(map(str, e[:18])), 2)
            k = 3
        dlc = w[k] & 0xF
        dat = [x & 0xFF for x in w[k + 1:k + 1 + dlc]]
        status = w[-1]
        dut._log.info(f"CAN RX id={ident_got:#x} dlc={dlc} data={dat} status={status:#x} ack={ack_seen}")
        assert ident_got == ident and bool(hdr[13]) == ext
        if corrupt:
            assert status != 0 and ack_seen == [1], "corrupted frame must not be ACKed"
        else:
            assert dlc == len(data) and dat == data and status == 0 and ack_seen == [0]


# ---------------------------------------------------------------- USB low speed
def crc16_usb(bits):
    crc = 0xFFFF
    for b in bits:
        x = (crc ^ b) & 1
        crc >>= 1
        if x:
            crc ^= 0xA001
    return crc ^ 0xFFFF


def crc5_usb(bits):
    crc = 0x1F
    for b in bits:
        x = (crc ^ b) & 1
        crc >>= 1
        if x:
            crc ^= 0x14
    return crc ^ 0x1F


def usb_packet(pid, payload_bits, crc_kind):
    """Reference: returns (unstuffed bits, line states 'J'/'K'/'0') for a low-speed packet."""
    bits = bits_lsb(0x80, 8) + bits_lsb(pid, 8) + list(payload_bits)
    if crc_kind == 16:
        bits += bits_lsb(crc16_usb(payload_bits), 16)
    elif crc_kind == 5:
        bits += bits_lsb(crc5_usb(payload_bits), 5)
    st, ones = [], 0
    for b in bits:
        st.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            st.append(0)
            ones = 0
    line, lvl = [], "J"
    for b in st:
        if b == 0:
            lvl = "K" if lvl == "J" else "J"
        line.append(lvl)
    return bits, line + ["0", "0", "J"]


def usb_state(dmin, dplus):
    if dmin == 0 and dplus == 0:
        return "0"
    return "J" if dmin == 1 and dplus == 0 else ("K" if dmin == 0 and dplus == 1 else "?")


USB16 = dict(poly=0x8005, crcw=15, osr_lsb=1, isr_lsb=1, stuff_n=6, stuff_ones=1, nrzi=1, diff=1)
USB5 = dict(USB16, poly=0x05, crcw=4)


@cocotb.test()
async def usb_tx(dut):
    h = await setup(dut)
    # reference CRC self-checks (published check values for "123456789")
    msg = [b for ch in b"123456789" for b in bits_lsb(ch, 8)]
    assert crc16_usb(msg) == 0xB4C8 and crc5_usb(msg) == 0x19
    setup_pkt = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00]
    tests = [(0xC3, [b for x in setup_pkt for b in bits_lsb(x, 8)], 16, USB16),
             (0x4B, [], 16, USB16),
             (0x2D, bits_lsb(0x00, 7) + bits_lsb(0x0, 4), 5, USB5),
             (0x69, bits_lsb(0x3A, 7) + bits_lsb(0x1, 4), 5, USB5)]
    for pid, payload, ck, cfgd in tests:
        await h.load(0, fw("usb_tx"), pe16.Cfg(**cfgd))
        await ClockCycles(dut.clk, 40)
        assert usb_state(pin(dut, 2), pin(dut, 3)) == "J"
        _, ref = usb_packet(pid, payload, ck)
        words = [(pid << 8) | 0x80, len(payload)] + pack_words(payload, True) + [ck - 1]

        async def sample():
            while pin(dut, 2) == 1:
                await RisingEdge(dut.clk)
            await ClockCycles(dut.clk, 16)
            got = []
            for _ in range(len(ref)):
                got.append(usb_state(pin(dut, 2), pin(dut, 3)))
                await ClockCycles(dut.clk, 32)
            return got

        cap = cocotb.start_soon(sample())
        await feed(h, words)
        got = await cap
        assert got == ref, f"USB pid {pid:#x}\n got {''.join(got)}\n ref {''.join(ref)}"
        dut._log.info(f"USB TX pid {pid:#04x}: {len(ref)} line states match ({''.join(got)})")


@cocotb.test()
async def usb_rx(dut):
    h = await setup(dut)
    setup_pkt = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00]
    tests = [(0xC3, [b for x in setup_pkt for b in bits_lsb(x, 8)], 16, USB16, 0x800D, False),
             (0xC3, [b for x in setup_pkt for b in bits_lsb(x, 8)], 16, USB16, 0x800D, True),
             (0x2D, bits_lsb(0x05, 7) + bits_lsb(0x2, 4), 5, USB5, 0x0C, False)]
    for pid, payload, ck, cfgd, resid, corrupt in tests:
        await h.load(0, fw("usb_rx"), pe16.Cfg(**cfgd))
        await h.push(0, [resid])
        dut.uio_in.value = 0x04 | 0xF3
        await ClockCycles(dut.clk, 100)
        bits, line = usb_packet(pid, payload, ck)
        if corrupt:                                   # flip one payload bit on the line
            payload2 = list(payload)
            payload2[5] ^= 1
            bits2, line2 = usb_packet(pid, payload2, 0)
            crc_bits = bits[len(bits) - ck:]
            _, line = usb_packet(pid, payload2 + [], 0)
            # rebuild with the original CRC appended
            allb = bits_lsb(0x80, 8) + bits_lsb(pid, 8) + payload2 + crc_bits
            st, ones = [], 0
            for b in allb:
                st.append(b)
                ones = ones + 1 if b else 0
                if ones == 6:
                    st.append(0)
                    ones = 0
            line, lvl = [], "J"
            for b in st:
                lvl = ("K" if lvl == "J" else "J") if b == 0 else lvl
                line.append(lvl)
            line += ["0", "0", "J"]
        nexp = 1 + (len(bits) - 16) // 16 + 3
        drain = cocotb.start_soon(stream(dut, h, [], nexp, 32 * (len(line) + 60)))
        # default run: one packet at -0.3% bit-rate error (measured tolerance +0.4/-0.6%)
        fu = float(os.environ.get("USB_F", str([1.0, 0.997, 1.0][tests.index((pid, payload, ck, cfgd, resid, corrupt)) % 3])))
        tt = 0
        for i, st in enumerate(line + ["J"] * 4):
            dm, dp = {"J": (1, 0), "K": (0, 1), "0": (0, 0)}[st]
            dut.uio_in.value = 0xF3 | (dm << 2) | (dp << 3)
            nx = round((i + 1) * 32 * fu)
            await ClockCycles(dut.clk, nx - tt)
            tt = nx
        w = await drain
        assert len(w) == nexp, f"got {len(w)} words, expected {nexp}"
        sync_pid, full, part, rem, status = w[0], w[1:-3], w[-3], w[-2], w[-1]
        k = 15 - rem
        got = [b for x in full for b in bits_lsb(x, 16)] + bits_lsb(part >> (16 - k), k) if k else \
            [b for x in full for b in bits_lsb(x, 16)]
        exp = bits[16:] if not corrupt else None
        dut._log.info(f"USB RX pid {pid:#04x} corrupt={corrupt}: sync/pid {sync_pid:#06x}, "
                      f"{len(got)} data bits, status {status}")
        assert sync_pid == (pid << 8) | 0x80
        if corrupt:
            assert status == 0
        else:
            assert got == exp and status == 1


# ---------------------------------------------------------------- 10BASE-T style Manchester
@cocotb.test()
async def eth_manchester(dut):
    h = await setup(dut)
    await h.load(0, fw("eth_tx"), pe16.Cfg(osr_lsb=1))
    frame = [0x55] * 7 + [0xD5] + [0xFF] * 6 + [0x02, 0x00, 0x00, 0x00, 0x00, 0x01, 0x88, 0xB5]
    frame += [0x12, 0x34, 0xA5, 0x5A]
    bits = [b for x in frame for b in bits_lsb(x, 8)]
    await ClockCycles(dut.clk, 20)
    cap = cocotb.start_soon(edges(dut, 0, C['us'](80) + 200 + 24 * len(bits) + 800))
    await feed(h, [len(bits) - 1] + pack_words(bits, True))
    ev = await cap
    # bit 0 of the preamble is 1: line low, rising at mid-bit. Mid-bit transitions every 24
    # clocks: rising = 1, falling = 0.
    tt = ev[0][0] - 12
    mids = []
    for k in range(len(bits)):
        tr = [l for t, l in ev if tt + 6 <= t <= tt + 18]
        mids.append(tr[0] if tr else None)
        tt += 24
    assert mids == bits, "Manchester decode mismatch"
    await RisingEdge(dut.clk)
    assert (await h.status(0))["ov"] == 0, "half-bit deadline missed"
    # normal link pulses while idle: width and spacing, measured with edge triggers
    times = []
    while len(times) < 4:
        await Edge(dut.uio_out)
        await ReadOnly()
        times.append((get_sim_time("ns") / 10, pin(dut, 0)))
        await RisingEdge(dut.clk)
    r = [t for t, l in times if l == 1]
    f = [t for t, l in times if l == 0]
    width, gap = f[0] - r[0], r[1] - r[0]
    dut._log.info(f"Manchester: {len(bits)} bits decoded; NLP width {width:.0f} clocks, spacing {gap / MS:.3f} ms")
    assert width == C['ns'](100) and 8 * MS <= gap <= 24 * MS
