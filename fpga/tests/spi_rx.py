
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# SPI Mode 0 receiver, Engine 2
# Pin 1 = SCK
# Pin 2 = MOSI
# Pin 3 = CS_N
#
# MSB-first input, eight bits per transaction.

program = """
top:
    wait 1 3
    wait 0 3

    set ctl isr0
    set x 7

bit:
    wait 0 1
    wait 1 1
    in raw 2
    wait 0 1
    jmp x-- bit

    push
    wait 1 3
    jmp top
"""

words = pe16.assemble(program)

# Left-shifting ISR preserves MSB-first byte order.
cfg = pe16.Cfg(isr_lsb=0)


def w16(value):
    return [(value >> 8) & 0xFF, value & 0xFF]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("SPI Mode 0 RX - Engine 2")
print("SCK=DIO5, MOSI=DIO6, CS_N=DIO7")
print(f"Instructions: {len(words)}")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Stop/reset Engine 2", [0x87, 0x02])

show("3. Load SPI RX firmware", [
    0x81, 0x00
] + sum((w16(w) for w in words), []))

show("4. Configure ISR", [
    0x82, 0x01
] + w16(cfg.word(1)))

show("5. Start Engine 2", [0x87, 0x01])

show("6. Read FIFO count", [0x86, 0x00, 0x00])
show("7. Read/pop received word", [0x84, 0x00, 0x00])
