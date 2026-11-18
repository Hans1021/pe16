
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# Continuous UART TX, 38400 baud, 8N1.
# Engine 0, physical pin 0, repeating 0x55.
# No TX FIFO data required.

program = """
    set oe 0 1
    set pin 0 1

top:
    set x 85
    mov osr x
    set ctl sync
    set pin 0 0
    set y 7

bit:
    dly 1250
    out raw 0
    jmp y-- bit

    dly 1250
    set pin 0 1
    dly 1250
    jmp top
"""

words = pe16.assemble(program)
cfg = pe16.Cfg(osr_lsb=1)


def w16(value):
    return [(value >> 8) & 0xFF, value & 0xFF]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print(f"Program: {len(words)} instructions")
print("UART: 38400 baud, 8N1, repeating 0x55")
print("Engine 0 -> pin 0 -> AD3 DIO4")

show("1. Enable pin 0", [0x02, 0x03, 0x00, 0xFF])
show("2. Assign pin 0 to Engine 0", [0x02, 0x02, 0x00, 0xE4])
show("3. Stop/reset Engine 0", [0x07, 0x02])

show(
    "4. Load firmware",
    [0x01, 0x00] + sum((w16(w) for w in words), []),
)

show("5. Configure CRC", [0x02, 0x00] + w16(cfg.word(0)))
show("6. Configure UART", [0x02, 0x01] + w16(cfg.word(1)))
show("7. Start Engine 0", [0x07, 0x01])
show("8. Read status", [0x05, 0x00, 0x00])
