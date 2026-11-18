
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# I2C master, Engine 1
# SCL = pin 1 / DIO5
# SDA = pin 2 / DIO6
#
# Open-drain:
#   OE = 1, output value 0 -> LOW
#   OE = 0                 -> released HIGH
#
# Send address 0x50, write bit 0: 0xA0
# No slave connected: expect NACK.

T_HALF = 240
ADDRESS_BYTE = 0xA0

lines = [
    "set pin 1 0",
    "set pin 2 0",
    "set oe 1 0",
    "set oe 2 0",
    "dly 1000",
    "top:",
    # START: SDA falls while SCL remains HIGH
    "set oe 2 1",
    f"dly {T_HALF}",
    "set oe 1 1",
]

# Send eight bits, MSB first.
for i in range(7, -1, -1):
    bit = (ADDRESS_BYTE >> i) & 1

    # Released HIGH for bit=1, driven LOW for bit=0.
    lines += [
        f"set oe 2 {0 if bit else 1}",
        f"dly {T_HALF}",
        "set oe 1 0",
        f"dly {T_HALF}",
        "set oe 1 1",
    ]

# Ninth clock: release SDA, receive NACK.
lines += [
    "set oe 2 0",
    f"dly {T_HALF}",
    "set oe 1 0",
    f"dly {T_HALF}",
    "set oe 1 1",
    # STOP: release SDA while SCL is HIGH.
    "set oe 2 1",
    f"dly {T_HALF}",
    "set oe 1 0",
    f"dly {T_HALF}",
    "set oe 2 0",
    "dly 2000",
    "jmp top",
]

program = "\n".join(lines)
words = pe16.assemble(program)


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("I2C master TX, Engine 1")
print("SCL=DIO5, SDA=DIO6")
print("Address=0x50, Write, NACK expected")
print(f"Program size: {len(words)} instructions")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Stop/reset Engine 2", [0x87, 0x02])
show("3. Stop/reset Engine 3", [0xC7, 0x02])

show("4. Set pin ownership", [
    0x02, 0x02, 0x00, 0x54
])

show("5. Enable protocol pins", [
    0x02, 0x03, 0x00, 0xFF
])

show("6. Load I2C firmware", [
    0x41, 0x00
] + [b for word in words for b in w16(word)])

show("7. Start Engine 1", [0x47, 0x01])

show("8. Read status", [0x45, 0x00, 0x00])
