
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# Engine 2 UART RX, pin 2, 38400 baud, 8N1
# RX FIFO stores received byte in bits [15:8].

program = """
.equ T 1250
.equ L 3

top:
    wait 1 2
    wait 0 2
    set ctl sync isr0
    set x 7
    dly {T // 2 - L}

bit:
    dly {T}
    in raw 2
    jmp x-- bit

    push
    dly {T}
    jmp top
"""

words = pe16.assemble(program)
cfg = pe16.Cfg(isr_lsb=1)


def w16(value):
    return [(value >> 8) & 0xFF, value & 0xFF]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("Engine 2 UART RX: 38400 baud, 8N1")
print("Input: JA3 / AD3 DIO6")
print(f"Instructions: {len(words)}")

show("1. Stop/reset", [0x87, 0x02])

show(
    "2. Load RX firmware",
    [0x81, 0x00] + sum((w16(w) for w in words), []),
)

show("3. Configure UART", [0x82, 0x01] + w16(cfg.word(1)))
show("4. Start Engine 2", [0x87, 0x01])
show("5. Read FIFO counts", [0x86, 0x00, 0x00])
show("6. Read/pop RX word", [0x84, 0x00, 0x00])
