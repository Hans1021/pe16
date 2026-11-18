
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# 1-Wire timing demonstration
# Engine 1, pin 2 (JA3 / AD3 DIO6)
# 48 MHz clock
#
# Open-drain: output always 0.
# OE=1 drives LOW, OE=0 releases HIGH.
# Repeated pattern: reset + byte 0xA5, LSB-first.

PIN = 2
BYTE = 0xA5

lines = [
    f"set pin {PIN} 0",
    f"set oe {PIN} 0",
    "dly 1000",
    "top:",
    # Reset pulse: 480 us
    f"set oe {PIN} 1",
]

lines += ["dly 3840"] * 6

lines += [
    f"set oe {PIN} 0",
    # Recovery/presence window: 480 us.
    # No slave connected, so no presence pulse expected.
]
lines += ["dly 3840"] * 6

# Eight write slots, LSB-first.
for bit_index in range(8):
    bit = (BYTE >> bit_index) & 1

    lines.append(f"set oe {PIN} 1")

    if bit:
        # Write 1: short LOW, then release.
        lines += [
            "dly 288",             # 6 us
            f"set oe {PIN} 0",
            "dly 3072",            # 64 us
        ]
    else:
        # Write 0: long LOW, then release.
        lines += [
            "dly 2880",            # 60 us
            f"set oe {PIN} 0",
            "dly 480",             # 10 us
        ]

# Idle before repeating.
lines += [
    "dly 3840",
    "jmp top",
]

words = pe16.assemble("\n".join(lines))


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("1-Wire TX timing test")
print("Engine 1, JA3 / DIO6")
print("Pattern: reset + 0xA5 LSB-first")
print(f"Instructions: {len(words)}")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Stop/reset Engine 2", [0x87, 0x02])
show("3. Set pin ownership", [0x02, 0x02, 0x00, 0x54])
show("4. Enable pins", [0x02, 0x03, 0x00, 0xFF])

show(
    "5. Load firmware",
    [0x41, 0x00] + [b for w in words for b in w16(w)],
)

show("6. Start Engine 1", [0x47, 0x01])
