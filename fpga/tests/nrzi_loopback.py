
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# NRZI loopback, 100 kbps, 48 MHz system clock.
# Engine 1: transmitter on pin 2.
# Engine 2: receiver on pin 2.
#
# NRZI: 0 = toggle, 1 = hold.
# One byte (0xA6), LSB-first, then halt.

PIN = 2
BYTE = 0xA6
BIT_CYCLES = 480

tx_program = f"""
    set pin {PIN} 0
    set oe {PIN} 1
    set x {BYTE}
    mov osr x
    set y 7
    dly 1000

bit:
    out raw {PIN}
    dly {BIT_CYCLES}
    jmp y-- bit

    halt
"""

# First transmitted bit is 0, producing a rising edge.
# Sample the first bit halfway through its interval.
# NRZI decoding compares each sample with the
# preceding sampled line level.

rx_program = f"""
top:
    wait 0 {PIN}
    wait 1 {PIN}
    set ctl sync isr0
    dly {BIT_CYCLES // 2}
    in raw {PIN}

    set x 6

bit:
    dly {BIT_CYCLES}
    in raw {PIN}
    jmp x-- bit

    push
    halt
"""

tx_words = pe16.assemble(tx_program)
rx_words = pe16.assemble(rx_program)

# Enable the built-in NRZI encoder/decoder.
# Both shift registers operate LSB-first.
cfg = pe16.Cfg(
    osr_lsb=1,
    isr_lsb=1,
    nrzi=1,
)


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print("NRZI TX/RX loopback")
print("TX: Engine 1, RX: Engine 2")
print("Shared pin: JA3 / DIO6")
print("Data: 0xA6, LSB-first")
print("Bit rate: 100 kbps")
print("Expected RX word: 0xA600")
print(f"TX instructions: {len(tx_words)}")
print(f"RX instructions: {len(rx_words)}")

show("1. Reset Engine 1", [0x47, 0x02])
show("2. Reset Engine 2", [0x87, 0x02])

show("3. Pin ownership", [
    0x02, 0x02, 0x00, 0x54
])

show("4. Pin validity", [
    0x02, 0x03, 0x00, 0xFF
])

show("5. Load TX firmware", [
    0x41, 0x00
] + [b for w in tx_words for b in w16(w)])

show("6. Load RX firmware", [
    0x81, 0x00
] + [b for w in rx_words for b in w16(w)])

show("7. Configure Engine 1 NRZI", [
    0x42, 0x01
] + w16(cfg.word(1)))

show("8. Configure Engine 2 NRZI", [
    0x82, 0x01
] + w16(cfg.word(1)))

show("9. Start receiver", [0x87, 0x01])
show("10. Start transmitter", [0x47, 0x01])

show("11. RX FIFO count", [0x86, 0x00, 0x00])
show("12. RX FIFO pop", [0x84, 0x00, 0x00])
show("13. TX status", [0x45, 0x00, 0x00])
show("14. RX status", [0x85, 0x00, 0x00])
