
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "isa"))

import pe16

# PWM output: Engine 1, pin 2 (JA3 / DIO6)
# 48 MHz clock, approximately 12 kHz.
# Run with: python3 fpga/tests/pwm.py 25
#       or: python3 fpga/tests/pwm.py 75

duty = int(sys.argv[1]) if len(sys.argv) > 1 else 25

if duty not in (25, 75):
    raise ValueError("Duty cycle must be 25 or 75")

PERIOD = 4000
high_cycles = PERIOD * duty // 100
low_cycles = PERIOD - high_cycles

program = f"""
    set oe 2 1

top:
    set pin 2 1
    dly {high_cycles}
    set pin 2 0
    dly {low_cycles}
    jmp top
"""

words = pe16.assemble(program)


def w16(value):
    return [(value >> 8) & 255, value & 255]


def show(label, data):
    print(f"\n{label}")
    print(", ".join(f"h{x:02X}" for x in data))


print(f"PWM: {duty}% duty, approximately 12 kHz")
print("Engine 1, JA3 / DIO6")

show("1. Stop/reset Engine 1", [0x47, 0x02])

show("2. Set pin ownership", [
    0x02, 0x02, 0x00, 0x54
])

show("3. Enable protocol pins", [
    0x02, 0x03, 0x00, 0xFF
])

show("4. Load PWM firmware", [
    0x41, 0x00
] + [b for word in words for b in w16(word)])

show("5. Start Engine 1", [0x47, 0x01])
