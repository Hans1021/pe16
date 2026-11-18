
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# Manchester loopback at 100 kbps
# Engine 1: TX
# Engine 2: RX
# Shared pin 2 = JA3 / DIO6
#
# Convention:
# 1 = LOW -> HIGH
# 0 = HIGH -> LOW
#
# Receive by sampling the second half of each bit.

PIN = 2
BYTE = 0x96
HALF_BIT = 240
BIT_PERIOD = 480

# ---------- Engine 1: transmitter ----------

tx_lines = [
    f"set pin {PIN} 1",
    f"set oe {PIN} 1",
    "dly 1000",
    "top:",
]

for i in range(7, -1, -1):
    bit = (BYTE >> i) & 1
    first = 0 if bit else 1
    second = 1 if bit else 0

    tx_lines += [
        f"set pin {PIN} {first}",
        f"dly {HALF_BIT}",
        f"set pin {PIN} {second}",
        f"dly {HALF_BIT}",
    ]

tx_lines += [
    f"set pin {PIN} 1",
    "dly 1000",
    "jmp top",
]

tx_words = pe16.assemble("\n".join(tx_lines))

# ---------- Engine 2: receiver ----------
#
# Wait for HIGH idle, then falling frame edge.
# Sample at approximately 7.5 us into the
# first bit, then every 10 us.
#
# Second half of Manchester encoding:
# HIGH -> data 1
# LOW  -> data 0

rx_program = f"""
top:
    wait 1 {PIN}
    wait 0 {PIN}
    set ctl sync isr0

    dly 360
    in raw {PIN}

    set x 6
bit:
    dly {BIT_PERIOD}
    in raw {PIN}
    jmp x-- bit

    push
    jmp top
"""

rx_words = pe16.assemble(rx_program)

# ISR shifts left, resulting byte in [7:0].
cfg = pe16.Cfg(isr_lsb=0)


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("Manchester TX/RX loopback")
print("TX: Engine 1")
print("RX: Engine 2")
print("Shared pin: JA3 / DIO6")
print("Data: repeating 0x96, MSB first")
print("Bit rate: approximately 100 kbps")
print(f"TX instructions: {len(tx_words)}")
print(f"RX instructions: {len(rx_words)}")

show("1. Stop/reset Engine 1", [0x47, 0x02])
show("2. Stop/reset Engine 2", [0x87, 0x02])

show("3. Configure pin ownership", [
    0x02, 0x02, 0x00, 0x54
])

show("4. Enable protocol pins", [
    0x02, 0x03, 0x00, 0xFF
])

show("5. Load TX firmware", [
    0x41, 0x00
] + [b for w in tx_words for b in w16(w)])

show("6. Load RX firmware", [
    0x81, 0x00
] + [b for w in rx_words for b in w16(w)])

show("7. Configure RX ISR", [
    0x82, 0x01
] + w16(cfg.word(1)))

show("8. Start Engine 2 RX", [0x87, 0x01])
show("9. Start Engine 1 TX", [0x47, 0x01])

show("10. Read RX FIFO count", [
    0x86, 0x00, 0x00
])

show("11. Read/pop RX word", [
    0x84, 0x00, 0x00
])
