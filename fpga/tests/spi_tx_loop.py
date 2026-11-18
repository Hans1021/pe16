
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# SPI Mode 0 transmitter
# Engine 1
# Pin 1 = SCK
# Pin 2 = MOSI
# Pin 3 = CS_N
#
# Repeated byte: 0x69
# MSB first, approximately 100 kHz SCK

program = """
    set oe 1 1
    set oe 2 1
    set oe 3 1

    set pin 1 0
    set pin 2 0
    set pin 3 1

top:
    set x 105
    mov osr x
    set y 7

    set pin 3 0

bit:
    out raw 2
    dly 240
    set pin 1 1
    dly 240
    set pin 1 0
    jmp y-- bit

    set pin 3 1
    dly 1000
    jmp top
"""

words = pe16.assemble(program)

# SPI sends MSB first.
cfg = pe16.Cfg(osr_lsb=1, isr_lsb=1)


def w16(value):
    return [(value >> 8) & 0xFF, value & 0xFF]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("SPI Mode 0 TX - Engine 1")
print("SCK = DIO5, MOSI = DIO6, CS_N = DIO7")
print("Data: repeating 0x69, MSB first")
print(f"Instructions: {len(words)}")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Stop/reset Engine 2", [0x87, 0x02])
show("3. Stop/reset Engine 3", [0xC7, 0x02])

# Assign pins 1, 2, 3 to Engine 1.
show("4. Configure pin ownership", [
    0x02, 0x02, 0x00, 0x54
])

show("5. Enable protocol pins", [
    0x02, 0x03, 0x00, 0xFF
])

show("6. Load SPI firmware", [
    0x41, 0x00
] + sum((w16(w) for w in words), []))

show("7. Configure MSB-first", [
    0x42, 0x01
] + w16(cfg.word(1)))

show("8. Start Engine 1", [0x47, 0x01])

show("9. Read Engine 1 status", [
    0x45, 0x00, 0x00
])
