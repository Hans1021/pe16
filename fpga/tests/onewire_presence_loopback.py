
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# 1-Wire presence detection, 48 MHz
# Engine 1: master on pin 2 (JA3)
# Engine 2: slave on pin 3 (JA4)
# JA3 and JA4 connected; pull-up to 3.3 V.
#
# Open-drain outputs: value always LOW.
# OE=1 drives LOW; OE=0 releases the bus.

master = [
    "set pin 2 0",
    "set oe 2 0",
    "dly 1000",

    "; Reset pulse: 480 us",
    "set oe 2 1",
]

master += ["dly 3840"] * 6

master += [
    "; Release bus and sample presence after 70 us",
    "set oe 2 0",
    "dly 3360",
    "set ctl isr0",
    "in raw 2",
    "push",
    "halt",
]

slave = [
    "set pin 3 0",
    "set oe 3 0",

    "; Detect reset pulse and its release",
    "wait 1 2",
    "wait 0 2",
    "wait 1 2",

    "; Wait 30 us",
    "dly 1440",

    "; Presence pulse: LOW for 120 us",
    "set oe 3 1",
    "dly 2880",
    "dly 2880",
    "set oe 3 0",
    "halt",
]

master_words = pe16.assemble("\n".join(master))
slave_words = pe16.assemble("\n".join(slave))


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("1-Wire presence loopback")
print("Master: Engine 1, JA3")
print("Slave: Engine 2, JA4")
print("Jumper: JA3 <-> JA4")
print("Expected presence bit: 0")

show("1. Reset master", [0x47, 0x02])
show("2. Reset slave", [0x87, 0x02])
show("3. Pin ownership", [0x02, 0x02, 0x00, 0x94])
show("4. Pin validity", [0x02, 0x03, 0x00, 0xFF])

show("5. Load master", [0x41, 0x00] +
     [b for w in master_words for b in w16(w)])

show("6. Load slave", [0x81, 0x00] +
     [b for w in slave_words for b in w16(w)])

show("7. Start slave", [0x87, 0x01])
show("8. Start master", [0x47, 0x01])

show("9. Master FIFO count", [0x46, 0x00, 0x00])
show("10. Master RX pop", [0x44, 0x00, 0x00])
show("11. Master status", [0x45, 0x00, 0x00])
show("12. Slave status", [0x85, 0x00, 0x00])
