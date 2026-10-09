"""Emit random traces from the Python golden model for the Lean cross-check."""
import sys, random, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "isa"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "test", "core"))
import pe16

def rand_instr(rng, n):
    op = rng.randrange(8)
    if op == 0:
        c = rng.randrange(12)  # 0..10 defined, 11 = undefined (never taken)
        r = ((c & 7) << 10) | ((c >> 3) << 9) | rng.randrange(n)
    elif op == 1: r = (rng.randrange(2) << 12) | (rng.randrange(8) << 9)
    elif op == 2: r = (1 << 12) if rng.random() < 0.1 else rng.randrange(0, 12)
    elif op == 5 and rng.random() < 0.3: r = (4 << 10) | rng.randrange(64)
    elif op == 7: r = rng.choices([0, 1, 2, 3], [45, 45, 2, 8])[0] << 11
    else: r = rng.randrange(1 << 13)
    return (op << 13) | r

N, CYC = int(sys.argv[1]), int(sys.argv[2])
out = sys.stdout
for seed in range(N):
    rng = random.Random(1000 + seed)
    cfg = pe16.Cfg(poly=rng.randrange(1 << 16), crcw=rng.randrange(16), osr_lsb=rng.randrange(2),
                   isr_lsb=rng.randrange(2), stuff_n=rng.randrange(8), stuff_ones=rng.randrange(2),
                   nrzi=rng.randrange(2), diff=rng.randrange(2))
    prog = [rand_instr(rng, 24) for _ in range(24)]
    if rng.random() < 0.3:  # stuffing stress: long equal-bit runs on OUT/IN stuff paths
        for j in range(24):
            if rng.random() < 0.6:
                prog[j] = (rng.choice([3, 4]) << 13) | (2 << 11) | (rng.randrange(8) << 8)
            elif rng.random() < 0.3:
                prog[j] = (6 << 13) | (2 << 10) | (7 << 7) | (rng.randrange(2) << 6)  # mov osr (~)null
    e = pe16.Engine(prog, cfg)
    out.write(f"C {cfg.poly} {cfg.crcw} {cfg.osr_lsb} {cfg.isr_lsb} {cfg.stuff_n} {cfg.stuff_ones} {cfg.nrzi} {cfg.diff}\n")
    out.write("P " + " ".join(map(str, prog)) + "\n")
    pins = rng.randrange(256)
    for _ in range(CYC):
        if rng.random() < 0.3: pins ^= 1 << rng.randrange(8)
        push = rng.randrange(1 << 16) if rng.random() < 0.15 else None
        pop = rng.random() < 0.15
        e.step(pins, push, pop)
        st = list(e.state())
        out.write(f"S {pins} {-1 if push is None else push} {int(pop)} " + " ".join(map(str, st)) + "\n")
