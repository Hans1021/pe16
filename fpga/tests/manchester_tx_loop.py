
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# Manchester encoding, IEEE 802.3 convention:
# 1 = LOW -> HIGH
# 0 = HIGH -> LOW
#
# Engine 1, pin 2 (JA3 / DIO6)
# 48 MHz clock, 100 kbps
# Repeating byte 0xA5, MSB first

PIN = 2
BYTE = 0xA5
HALF_BIT = 240  # 5 us = 240 cycles

lines = [
    f"set pin {PIN} 0",
    f"set oe {PIN} 1",
    "top:",
]

for i in range(7, -1, -1):
    bit = (BYTE >> i) & 1

    # 1: LOW/HIGH, 0: HIGH/LOW
    first = 0 if bit else 1
    second = 1 if bit else 0

    lines += [
        f"set pin {PIN} {first}",
        f"dly {HALF_BIT}",
        f"set pin {PIN} {second}",
        f"dly {HALF_BIT}",
    ]

lines += [
    f"set pin {PIN} 0",
    "dly 1000",
    "jmp top",
]

words = pe16.assemble("\n".join(lines))


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("Manchester TX - Engine 1")
print("Pin 2 / DIO6, 100 kbps")
print("Pattern: 0xA5, MSB first")
print(f"Instructions: {len(words)}")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Set pin ownership", [0x02, 0x02, 0x00, 0x54])
show("3. Enable protocol pins", [0x02, 0x03, 0x00, 0xFF])

show(
    "4. Load firmware",
    [0x41, 0x00] + [b for w in words for b in w16(w)],
)

show("5. Start Engine 1", [0x47, 0x01])
