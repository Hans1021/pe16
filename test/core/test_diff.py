"""Differential test: RTL pe_core vs golden ISS (isa/pe16.py), compared every cycle."""
import os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "isa"))
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ReadOnly
import pe16

FIELDS = ["pc", "x", "y", "osr", "isr", "crc", "pout", "poe", "b", "s", "ov", "since",
          "last", "run", "halt", "txc", "rxc", "rxl"]


def rtl_state(dut):
    u = dut.u
    return (int(u.pc.value), int(u.x.value), int(u.y.value), int(u.osr.value), int(u.isr.value),
            int(u.crc.value), int(u.pout.value), int(u.poe.value), int(u.b.value), int(u.s.value),
            int(u.ov.value), int(u.since.value), int(u.last.value), int(u.runl.value),
            int(u.halt.value), int(u.txc.value), int(u.rxc.value), int(u.rxl.value))


def rand_instr(rng, n):
    op = rng.randrange(8)
    if op == 0:
        c = rng.randrange(12)  # 0..10 defined, 11 = undefined (never taken)
        r = ((c & 7) << 10) | ((c >> 3) << 9) | rng.randrange(n)
    elif op == 1:
        r = (rng.randrange(2) << 12) | (rng.randrange(8) << 9)
    elif op == 2:
        r = (1 << 12) if rng.random() < 0.1 else rng.randrange(0, 12)
    elif op == 5 and rng.random() < 0.3:
        r = (4 << 10) | rng.randrange(64)
    elif op == 7:
        f = rng.choices([0, 1, 2, 3], [45, 45, 2, 8])[0]
        r = f << 11
    else:
        r = rng.randrange(1 << 13)
    return (op << 13) | r


COV = set()


def classify(ins, pre, post, cfg):
    op, r = ins >> 13, ins & 0x1FFF
    if pre[14]:
        return ('halted',)
    stalled = post[0] == pre[0] and op != 0
    k = ['JMP','WAIT','DLY','OUT','IN','SET','MOV','FIFO'][op]
    if op == 0:
        return (k, ((r >> 10) & 7) | (((r >> 9) & 1) << 3), post[0] != (pre[0] + 1) & 0xFF)
    if op in (3, 4):
        return (k, (r >> 11) & 3, post[9], cfg.nrzi) if (r >> 11) & 3 != 3 or op == 3 else (k, 3, 0, 0)
    if op == 6:
        return (k, (r >> 10) & 7, (r >> 5) & 1) if not (r >> 4) & 1 else (k, 'rev')
    if op == 5 and ((r >> 10) & 7) == 4 and (r & 32):
        return (k, 'sync')
    if op == 5:
        return (k, (r >> 10) & 7)
    if op == 2:
        return (k, 'stall' if stalled else ('overrun' if post[10] and not pre[10] else 'retire'))
    if op == 7:
        return (k, (r >> 11) & 3, 'stall' if stalled else 'go')
    return (k, 'stall' if stalled else 'go')


async def run_case(dut, seed, n_ins=24, cycles=600):
    rng = random.Random(seed)
    cfg = pe16.Cfg(poly=rng.randrange(1 << 16), crcw=rng.randrange(16), osr_lsb=rng.randrange(2),
                   isr_lsb=rng.randrange(2), stuff_n=rng.randrange(8), stuff_ones=rng.randrange(2),
                   nrzi=rng.randrange(2), diff=rng.randrange(2))
    prog = [rand_instr(rng, n_ins) for _ in range(n_ins)]
    if rng.random() < 0.3:  # stuffing stress: long equal-bit runs on OUT/IN stuff paths
        for j in range(n_ins):
            if rng.random() < 0.6:
                prog[j] = (rng.choice([3, 4]) << 13) | (2 << 11) | (rng.randrange(8) << 8)
            elif rng.random() < 0.3:
                prog[j] = (6 << 13) | (2 << 10) | (7 << 7) | (rng.randrange(2) << 6)
    # reset
    dut.run.value = 0
    dut.host_push.value = 0
    dut.host_pop.value = 0
    dut.rst_n.value = 0
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    dut.poly.value, dut.crcw.value = cfg.poly, cfg.crcw
    dut.osr_lsb.value, dut.isr_lsb.value = cfg.osr_lsb, cfg.isr_lsb
    dut.stuff_n.value, dut.stuff_ones.value = cfg.stuff_n, cfg.stuff_ones
    dut.nrzi.value, dut.diff.value = cfg.nrzi, cfg.diff
    for a in range(256):
        dut.we.value, dut.waddr.value, dut.wdata.value = 1, a, (prog[a] if a < n_ins else 0)
        await FallingEdge(dut.clk)
    dut.we.value = 0
    await FallingEdge(dut.clk)          # let the RAM present address 0 (pc=0)
    await FallingEdge(dut.clk)
    iss = pe16.Engine(prog, cfg)
    dut.run.value = 1
    pins = rng.randrange(256)
    for cyc in range(cycles):
        if rng.random() < 0.3:
            pins ^= 1 << rng.randrange(8)
        push = rng.randrange(1 << 16) if rng.random() < 0.15 else None
        pop = rng.random() < 0.15
        dut.pin_in.value = pins
        dut.host_push.value = 1 if push is not None else 0
        dut.host_wdata.value = push or 0
        dut.host_pop.value = 1 if pop else 0
        await RisingEdge(dut.clk)
        pre = iss.state()
        ins = iss.imem[iss.pc]
        iss.step(pins, push, pop)
        COV.add(classify(ins, pre, iss.state(), cfg))
        if iss.run == 7:
            COV.add(('run7',))
        await ReadOnly()
        got, exp = rtl_state(dut), iss.state()
        if got != exp:
            diff = {f: (g, e) for f, g, e in zip(FIELDS, got, exp) if g != e}
            raise AssertionError(f"seed {seed} cycle {cyc} pc={exp[0]} ins={prog[exp[0]] if exp[0] < n_ins else 0:04x} "
                                 f"mismatch (rtl, iss): {diff}")
        await FallingEdge(dut.clk)
    return iss


@cocotb.test()
async def diff_random(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    n = int(os.environ.get("NSEEDS", "200"))
    cover = [0] * 8
    for seed in range(n):
        await run_case(dut, seed)
    expect = set()
    for c in range(9):
        expect |= {('JMP', c, True), ('JMP', c, False)}
    for c in (9, 10):
        expect |= {('JMP', c, True), ('JMP', c, False)}
    expect.add(('JMP', 11, False))
    for m in range(4):
        for k in ('OUT', 'IN'):
            for z in (0, 1):
                expect.add((k, m, 0, z) if not (k == 'IN' and m == 3) else (k, 3, 0, 0))
    expect -= {('OUT', 3, 0, 1)}
    expect |= {('OUT', 2, 1, 0), ('OUT', 2, 1, 1), ('IN', 2, 1, 0), ('IN', 2, 1, 1), ('MOV', 'rev')}
    expect |= {('SET', d) for d in range(8)} | {('MOV', d, a) for d in range(8) for a in (0, 1)} | {('SET', 'sync')}
    expect |= {('DLY', 'stall'), ('DLY', 'retire'), ('DLY', 'overrun'), ('WAIT', 'stall'), ('WAIT', 'go')}
    expect |= {('FIFO', 0, 'stall'), ('FIFO', 0, 'go'), ('FIFO', 1, 'stall'), ('FIFO', 1, 'go'), ('FIFO', 2, 'stall'), ('FIFO', 3, 'go'), ('halted',), ('run7',)}
    miss = sorted(map(str, expect - COV))
    dut._log.info(f"coverage: {len(expect & COV)}/{len(expect)} bins hit; missing: {miss}")
    assert not miss
    dut._log.info(f"{n} random programs x 600 cycles: RTL == ISS on all 17 state fields every cycle")
