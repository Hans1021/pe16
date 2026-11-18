
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# I2C ACK loopback at approximately 100 kHz.
#
# Engine 1:
#   SCL pin 1 / JA2
#   SDA pin 2 / JA3
#
# Engine 2:
#   ACK pin 3 / JA4
#
# JA3 and JA4 connected with a jumper.
# Both SDA drivers use open-drain (output LOW only).
#
# One address transaction: 0x50, write.
# Engine 2 ACKs, and Engine 1 records the ACK bit.

HALF = 240
ADDR = 0xA0

# ---------- Engine 1: master ----------

master = [
    "set pin 1 0",
    "set pin 2 0",
    "set oe 1 0",
    "set oe 2 0",
    "dly 1000",

    # START: SDA goes LOW while SCL is HIGH.
    "set oe 2 1",
    f"dly {HALF}",
    "set oe 1 1",
    "set ctl isr0",
]

# Send 8 address bits, MSB first.
for i in range(7, -1, -1):
    bit = (ADDR >> i) & 1
    master += [
        f"set oe 2 {0 if bit else 1}",
        f"dly {HALF}",
        "set oe 1 0",
        f"dly {HALF}",
        "set oe 1 1",
    ]

# Ninth clock: slave ACK.
master += [
    "set oe 2 0",      # Release SDA
    f"dly {HALF}",
    "set oe 1 0",      # SCL HIGH
    f"dly {HALF}",
    "in raw 2",        # Sample ACK: 0 = ACK
    "set oe 1 1",      # SCL LOW
    "push",            # Store sampled bit

    # STOP condition.
    "set oe 2 1",
    f"dly {HALF}",
    "set oe 1 0",
    f"dly {HALF}",
    "set oe 2 0",

    "halt",
]

master_words = pe16.assemble("\n".join(master))

# ---------- Engine 2: slave ACK ----------

slave = """
    set pin 3 0
    set oe 3 0

    wait 1 2
    wait 0 2

    set x 7

bit:
    wait 0 1
    wait 1 1
    jmp x-- bit

    ; After 8 SCL pulses, wait for SCL LOW.
    wait 0 1

    ; ACK: pull SDA LOW during ninth clock.
    set oe 3 1
    wait 1 1
    wait 0 1
    set oe 3 0

    halt
"""

slave_words = pe16.assemble(slave)

# ISR left-shifts; ACK bit appears in bit 0.
cfg = pe16.Cfg(isr_lsb=0)


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("I2C bidirectional ACK loopback")
print("Master: Engine 1, address 0x50 WRITE")
print("Slave: Engine 2, ACK on shared SDA")
print("Jumper: JA3 <-> JA4")
print(f"Master instructions: {len(master_words)}")
print(f"Slave instructions: {len(slave_words)}")

show("1. Reset Engine 1", [0x47, 0x02])
show("2. Reset Engine 2", [0x87, 0x02])
show("3. Reset Engine 3", [0xC7, 0x02])

# Pin 1 -> E1, pin 2 -> E1, pin 3 -> E2.
show("4. Pin ownership", [0x02, 0x02, 0x00, 0x94])
show("5. Pin validity", [0x02, 0x03, 0x00, 0xFF])

show(
    "6. Load master",
    [0x41, 0x00] + [b for w in master_words for b in w16(w)],
)

show(
    "7. Load slave",
    [0x81, 0x00] + [b for w in slave_words for b in w16(w)],
)

show("8. Master ISR configuration",
     [0x42, 0x01] + w16(cfg.word(1)))

show("9. Start slave", [0x87, 0x01])
show("10. Start master", [0x47, 0x01])

show("11. Master RX count", [0x46, 0x00, 0x00])
show("12. Master RX pop", [0x44, 0x00, 0x00])
show("13. Master status", [0x45, 0x00, 0x00])
show("14. Slave status", [0x85, 0x00, 0x00])
