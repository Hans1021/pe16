# SPDX-License-Identifier: Apache-2.0
"""Cyber-physical / fieldbus protocols: Modbus RTU and MIL-STD-1553.
Reference models are written from the standards and see only pins."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "isa"))
import cocotb
from cocotb.triggers import ClockCycles, Edge, ReadOnly, Timer
from cocotb.utils import get_sim_time
from test import setup, fw
from test_protocols import stream, feed
import pe16

C = pe16.clock_helpers(pe16.CLOCK_HZ)


async def capture(dut, ncycles):
    """Record (clock, uio_out) at every change for ncycles, using edge triggers."""
    t0 = get_sim_time("ns")
    ev = [(0, int(dut.uio_out.value))]

    async def go():
        while True:
            await Edge(dut.uio_out)
            await ReadOnly()
            ev.append((round((get_sim_time("ns") - t0) / 10), int(dut.uio_out.value)))
    task = cocotb.start_soon(go())
    await Timer(ncycles * 10, "ns")
    task.cancel()
    return ev


def level_at(ev, t):
    v = ev[0][1]
    for te, ve in ev:
        if te > t:
            break
        v = ve
    return v


def bits_lsb(v, n):
    return [(v >> k) & 1 for k in range(n)]


# ================================================================ Modbus RTU
def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def modbus_adu(pdu):
    c = crc16_modbus(pdu)
    return list(pdu) + [c & 0xFF, c >> 8]


def char_8e1(b, bad_parity=False):
    par = bin(b).count("1") & 1          # even parity bit
    return [0] + bits_lsb(b, 8) + [par ^ bad_parity, 1]


MB_T = C["bit"](115200)
MB_T35 = max(C["us"](1750), 385 * MB_T // 10)
MB_CFG = pe16.Cfg(poly=0x8005, crcw=15, osr_lsb=1, isr_lsb=1)


def test_modbus_reference():
    # published values: CRC-16/MODBUS check, and the Modbus serial-line spec example frame
    assert crc16_modbus(b"123456789") == 0x4B37
    c = crc16_modbus([0x11, 0x03, 0x00, 0x6B, 0x00, 0x03])
    assert [c & 0xFF, c >> 8] == [0x76, 0x87]


def decode_8e1(ev, pin, start, T, maxchars):
    """Decode 8E1 characters starting at the falling edge `start`. Returns (bytes, parity_ok,
    stop_ok, end_time_of_last_stop)."""
    out, ok_p, ok_s, t = [], True, True, start
    for _ in range(maxchars):
        lv = lambda k: (level_at(ev, t + T // 2 + k * T) >> pin) & 1
        assert lv(0) == 0, "start bit"
        v = sum(lv(1 + k) << k for k in range(8))
        ok_p &= lv(9) == bin(v).count("1") & 1
        ok_s &= lv(10) == 1
        out.append(v)
        end = t + 11 * T
        nxt = [te for te, ve in ev if te >= end - 2 and not (ve >> pin) & 1 and (level_at(ev, te - 1) >> pin) & 1]
        if not nxt or nxt[0] > end + 2:
            return out, ok_p, ok_s, end, (nxt[0] if nxt else None)
        t = nxt[0]
    return out, ok_p, ok_s, end, None


@cocotb.test()
async def modbus_tx(dut):
    test_modbus_reference()
    h = await setup(dut)
    await h.load(0, fw("modbus_tx"), MB_CFG)
    await ClockCycles(dut.clk, 20)
    f1 = [0x11, 0x03, 0x00, 0x6B, 0x00, 0x03]
    f2 = [0x01, 0x06, 0x00, 0x01, 0x00, 0x03]
    dur = 2 * 8 * 11 * MB_T + MB_T35 + 4 * MB_T + 30000
    cap = cocotb.start_soon(capture(dut, dur))
    await feed(h, [len(f1) + 1] + f1 + [len(f2) + 1] + f2)
    ev = await cap
    falls = [te for (te, ve), (_, vp) in zip(ev[1:], ev) if not ve & 1 and vp & 1]
    b1, p1, s1, end1, nxt = decode_8e1(ev, 0, falls[0], MB_T, 20)
    assert nxt is not None, "second frame not seen"
    b2, p2, s2, end2, _ = decode_8e1(ev, 0, nxt, MB_T, 20)
    gap = nxt - end1
    de_rise = [te for (te, ve), (_, vp) in zip(ev[1:], ev) if ve & 4 and not vp & 4]
    de_fall = [te for (te, ve), (_, vp) in zip(ev[1:], ev) if not ve & 4 and vp & 4]
    dut._log.info(f"Modbus TX frame1 {[hex(x) for x in b1]} frame2 {[hex(x) for x in b2]}; "
                  f"gap {gap / MB_T:.1f} bits ({gap / C['us'](1):.0f} us); DE {de_rise[:2]} -> {de_fall[:2]}")
    assert b1 == modbus_adu(f1) and b2 == modbus_adu(f2), "bytes or CRC wrong"
    assert b1[-2:] == [0x76, 0x87]
    assert p1 and p2 and s1 and s2, "parity or stop bit wrong"
    assert gap >= MB_T35, "t3.5 silence between frames violated"
    assert de_rise[0] <= falls[0] and de_fall[0] >= end1, "DE must cover the whole first frame"
    assert de_rise[1] <= nxt and de_fall[1] >= end2


@cocotb.test()
async def modbus_rx(dut):
    h = await setup(dut)
    await h.load(0, fw("modbus_rx"), MB_CFG)
    polls = -(-MB_T35 // 16)
    await h.push(0, [polls])
    await ClockCycles(dut.clk, 50)
    good = modbus_adu([0x11, 0x03, 0x00, 0x6B, 0x00, 0x03])
    badcrc = list(good)
    badcrc[-1] ^= 0x01
    wr = modbus_adu([0x01, 0x06, 0x00, 0x01, 0x00, 0x03])
    # parity errors on an even-weight byte (0x00) and on an odd-weight byte (0x01): both decoder paths
    frames = [(good, None), (badcrc, None), (wr, 2), (wr, 3), (good, None)]
    nexp = sum((len(f) if bp is None else bp) + 1 for f, bp in frames)   # data words + status
    drain = cocotb.start_soon(stream(dut, h, [], nexp, 10 * (8 * 11 * MB_T + 2 * MB_T35)))
    for f, bad_at in frames:
        for i, b in enumerate(f):
            for bit in char_8e1(b, bad_parity=(i == bad_at)):
                dut.uio_in.value = 0xFD | (bit << 1)
                await ClockCycles(dut.clk, MB_T)
        await ClockCycles(dut.clk, MB_T35 + 3 * MB_T)
    w = await drain
    dut._log.info(f"Modbus RX pushes: {[hex(x) for x in w]}")
    assert len(w) == nexp, f"{len(w)} words, expected {nexp}"
    k, got = 0, []
    for f, bad_at in frames:
        n = len(f) if bad_at is None else bad_at
        data = [x >> 8 for x in w[k:k + n]]
        marks = [x & 0xFF for x in w[k:k + n]]
        got.append((data, w[k + n]))
        assert marks == [0xFF] * n, "character marker"
        k += n + 1
    assert got[0] == (good, 0), "valid frame must end with status 0"
    assert got[1] == (badcrc, 1), "CRC error must end with status 1"
    assert got[2][1] == 2 and got[3][1] == 2, "parity error must give status 2"
    assert got[4] == (good, 0), "receiver must recover after an error"


# ================================================================ MIL-STD-1553
M_H = C["ns"](500)


def m1553_halves(words, first_sync="cmd"):
    """Reference encoder: list of half-bit levels (+1 / -1) for back-to-back words."""
    out = []
    for i, w in enumerate(words):
        sync = first_sync if i == 0 else "data"
        out += [1, 1, 1, -1, -1, -1] if sync == "cmd" else [-1, -1, -1, 1, 1, 1]
        bits = [(w >> (15 - k)) & 1 for k in range(16)]
        bits.append(1 - (sum(bits) & 1))          # odd parity
        for b in bits:
            out += [1, -1] if b else [-1, 1]
    return out


@cocotb.test()
async def mil1553_tx(dut):
    h = await setup(dut)
    await h.load(0, fw("mil1553_tx"), pe16.Cfg(osr_lsb=0))
    await ClockCycles(dut.clk, 20)
    words = [0x1843, 0xBEEF, 0x0000, 0xFFFF]
    ref = m1553_halves(words)
    cap = cocotb.start_soon(capture(dut, len(ref) * M_H + 2000))
    await feed(h, [len(words) - 1] + words)
    ev = await cap
    act = [te for te, ve in ev if ve & 3]
    t0 = act[0]
    got = []
    for k in range(len(ref)):
        v = level_at(ev, t0 + k * M_H + M_H // 2) & 3
        got.append({1: 1, 2: -1}.get(v, 0))
    edges = [te - t0 for (te, ve), (_, vp) in zip(ev[1:], ev) if te >= t0 and (ve ^ vp) & 3]   # line pins only
    off = sorted(set(e % M_H for e in edges))
    bad = [(e // M_H, e % M_H) for e in edges if e % M_H]
    dut._log.info(f"off-grid edges (half-bit index, offset): {bad[:12]}")
    idle_after = level_at(ev, t0 + len(ref) * M_H + M_H) & 3
    dut._log.info(f"1553 TX: {len(words)} words, {len(ref)} half-bits match={got == ref}, "
                  f"edge offsets mod H {off}, idle after: {idle_after == 0}")
    assert got == ref and off == [0] and idle_after == 0


@cocotb.test()
async def mil1553_rx(dut):
    h = await setup(dut)
    dut.uio_in.value = 0xF3                  # bus idle: RX+ and RX- both low
    await h.load(0, fw("mil1553_rx"), pe16.Cfg(isr_lsb=0))
    await ClockCycles(dut.clk, 20)

    msgs = [(m1553_halves([0x1800, 0x1234, 0xFFFF]), [(0x1800, 1), (0x1234, 2), (0xFFFF, 2)])]
    bad_par = m1553_halves([0x5555], "data")
    bad_par[-2], bad_par[-1] = -bad_par[-2], -bad_par[-1]          # flip the parity bit
    msgs.append((bad_par, [(None, 0x20)]))
    bad_par_o = m1553_halves([0x0001], "data")                        # odd weight: other parity path
    bad_par_o[-2], bad_par_o[-1] = -bad_par_o[-2], -bad_par_o[-1]
    msgs.append((bad_par_o, [(None, 0x20)]))
    # a missing mid-bit transition on a 0 and on a 1, in both parity states of the decoder
    for word, k in ((0x0000, 0), (0x8000, 1), (0x8000, 0), (0x0F0F, 5)):
        bad_man = m1553_halves([word], "cmd")
        bad_man[6 + 2 * k + 1] = bad_man[6 + 2 * k]
        msgs.append((bad_man, [(None, 0x10)]))
    msgs.append((m1553_halves([0x0C22]), [(0x0C22, 1)]))
    nexp = 2 * sum(len(e) for _, e in msgs)
    drain = cocotb.start_soon(stream(dut, h, [], nexp, 800000))
    fm = float(os.environ.get("M1553_F", "1.0"))
    for halves, _ in msgs:
        t = 0
        for i, lv in enumerate(halves):
            dut.uio_in.value = 0xF3 | ((lv > 0) << 2) | ((lv < 0) << 3)
            nx = round((i + 1) * M_H * fm)
            await ClockCycles(dut.clk, nx - t)
            t = nx
        dut.uio_in.value = 0xF3                                     # idle
        await ClockCycles(dut.clk, 60 * M_H)
    w = await drain
    pairs = list(zip(w[0::2], w[1::2]))
    dut._log.info(f"1553 RX: {[(hex(a), hex(b)) for a, b in pairs]}")
    exp = [e for _, es in msgs for e in es]
    assert len(pairs) == len(exp)
    for (d, st), (ed, est) in zip(pairs, exp):
        assert st == est and (ed is None or d == ed), f"got {d:#x}/{st:#x} expected {ed}/{est:#x}"
