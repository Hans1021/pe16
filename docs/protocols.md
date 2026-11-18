# PE16 Protocol Firmware

PE16 implements 16 communication protocols across 23 firmware programs in [fw/](../fw/). The programs execute on the same programmable protocol engines, using instruction-controlled pin timing, CRC, bit stuffing, and line coding.

All programs are tested through the SPI host interface using independent pin-level reference models in [test/](../test/). Timing constants are written as physical durations, such as `{us(70)}` and `{bit(500000)}`, and converted to cycles by the assembler at the 48 MHz system clock. Selected operations have also been demonstrated on a Basys3 FPGA.

## Protocol implementations

| Firmware | Protocol | Pins (`uio`) | Configuration | Test evidence | Scope and limitations |
|---|---|---|---|---|---|
| `uart_tx` / `uart_rx` | UART 8N1 | 0 TX / 1 RX | OSR/ISR LSB first | 4 TX + 3 RX bytes; TX edges at exact 16-clock intervals; FPGA 38,400-baud TX/RX | Baud set by `DLY` timing |
| `spi_ctl` | SPI controller, mode 0 | 2 SCK, 3 MOSI, 4 MISO, 5 CS | MSB first | 3 bytes each direction against target model; FPGA TX/RX at 100 kHz | Mode 0 |
| `spi_tgt` | SPI target, mode 0 | Same pins, reversed directions | MSB first | 3 bytes each direction against controller model | SCK half-period > approximately 5 clocks |
| `i2c_ctl` | I²C controller, fast mode | 0 SCL, 1 SDA, open-drain | MSB first | Write, two-byte read with ACK/NACK, absent-target NACK, clock stretching; FPGA master TX/ACK loopback | 7-bit addresses; no multi-controller arbitration |
| `i2c_tgt` | I²C target at `0x42` | 0 SCL, 1 SDA | MSB first | Byte write/read and rejection of unrelated addresses | One data byte per transaction |
| `jtag` | JTAG TMS/TDI/TDO sequencer | 0 TCK, 1 TMS, 2 TDI, 3 TDO | MSB first | IEEE 1149.1 TAP model, IDCODE read, return to Run-Test/Idle | Host constructs TMS path |
| `swd` | SWD read | 0 SWCLK, 1 SWDIO | LSB first | Line reset, DPIDR read, ACK OK, parity | Reads only; no WAIT/FAULT retry |
| `ps2_rx` | PS/2 device-to-host | 0 CLK, 1 DATA | LSB first | Two scan codes at 16.7 kHz device clock | Receive only |
| `onewire` | 1-Wire controller | 0 DQ, open-drain | LSB first | Reset/presence, Read ROM (`0x33`), two ROM bytes; FPGA reset/write and presence tests | Standard speed |
| `ws2812` | WS2812 LED stream | 0 | MSB first | 48 bits, HIGH 417/813 ns, period 1.25 µs | Host keeps TX FIFO supplied |
| `dmx` | DMX512 transmitter | 0 | LSB first | 90 µs break, 12 µs MAB, five 8N2 slots at 250 kbaud | Transmit only |
| `lin` | LIN master frame | 0 | LSB first | 13-bit break, one-bit delimiter, sync/PID/data/checksum at 19,200 baud | Host computes PID/checksum |
| `mdio` | MDIO Clause 22 | 0 MDC, 1 MDIO | MSB first | Write/readback, PHY ID; MDC HIGH/LOW 208 ns, period 417 ns | Clause 22 |
| `can_tx` | CAN 2.0A/B transmit, 500 kb/s | 6 TXD | Polynomial `0x4599`, CRC width 15, stuffing threshold 5 | 12 standard/extended frames bit-exact against ISO 11898-1 builder | No arbitration monitoring or ACK check |
| `can_rx` | CAN 2.0A/B receive + ACK, 500 kb/s | 7 RXD, 6 TXD | CRC-15, identical-bit destuffing, MSB first | Five good frames, including extended and eight-byte frames, ACKed; corrupted frame rejected | SOF/stuff-bit synchronization; no error-frame generation |
| `usb_tx` | USB low-speed transmit, 1.5 Mb/s | 2 D−, 3 D+ | NRZI, differential, six-one stuffing, CRC-5/16 | Eight-byte DATA0, empty DATA1, SETUP and IN tokens; line states checked against reference | Packet transmission |
| `usb_rx` | USB low-speed receive | 2 D−, 3 D+ | Same | Valid DATA0 and token accepted; corrupted DATA0 rejected | Packet reception; limited resynchronization |
| `eth_tx` | 10BASE-T-style Manchester framing | 0 TD+, 1 TD− | LSB first | 208-bit frame decoded; link pulses 104 ns wide, 16.000 ms apart | 2 Mb/s; host supplies preamble/FCS |
| `modbus_tx` | Modbus RTU transmit, 8E1 | 0 TX, 2 RS-485 DE | Polynomial `0x8005`, CRC width 16, LSB first | Two frames with parity, stop bits and CRC; example `11 03 00 6B 00 03` produces `76 87`; 1759 µs inter-frame silence | Even parity; host supplies byte count |
| `modbus_rx` | Modbus RTU receive | 1 RX | Same | Valid frame, corrupted CRC, parity errors on both parity states, recovery | Host supplies t3.5 polling count; no t1.5 check |
| `mil1553_tx` | MIL-STD-1553 transmit | 0 TX+, 1 TX− | MSB first | Command + three data words; 160 Manchester half-bits compared with reference, 500 ns edge grid | Word sequencing; external 1553 transceiver |
| `mil1553_rx` | MIL-STD-1553 receive | 2 RX+, 3 RX− | MSB first | Command + two data words; parity errors and missing mid-bit transitions in both states, recovery | Word reception; expects idle between messages |

The pin assignments above are firmware conventions. The host can assign each physical output pin to a selected engine through the global ownership and validity registers. Every engine can independently sample all protocol inputs.

The TX and RX FIFOs contain four 16-bit words each. Programs returning multiple words, including JTAG, CAN RX, and USB RX, can be serviced by the host during execution. `PULL` and `PUSH` stall when their corresponding FIFO cannot complete a transfer.

## Timing and protocol engines

The `DLY` instruction maintains deadline-relative timing. Instead of waiting a fixed duration after each instruction, it measures intervals from the previous deadline, so branch paths can have different instruction counts without accumulating timing drift.

Timing is checked directly against the externally observed protocol waveform. Representative simulation results include:

| Protocol | Measured timing |
|---|---|
| I²C fast mode | SCL LOW ≥1.5 µs, HIGH ≥1.1 µs, ≤384 kHz |
| MDIO | 208 ns HIGH/LOW, 417 ns period |
| 1-Wire | 480.1 µs reset; standard-speed slots |
| WS2812 | 417/813 ns HIGH, 1.25 µs bit period |
| DMX512 | 90 µs break, 12 µs MAB |
| LIN | 13-bit break, one-bit delimiter |
| Modbus RTU | 1759 µs inter-frame silence |
| MIL-STD-1553 | Line edges on 500 ns half-bit grid |

The timing assertions identified and helped correct two firmware defects during development: an insufficient I²C SCL LOW interval and a slightly short 1-Wire reset pulse.

### CRC, stuffing, and parity

CAN and USB use the built-in CRC and stuffing hardware. CAN employs CRC-15 with complementary-bit insertion after five consecutive identical bits. USB uses CRC-5 for tokens, CRC-16 for data, NRZI, six-one stuffing, and complementary differential signaling.

The protocol tests compare transmitted sequences with independent encoders and check reception of both valid and corrupted frames.

Modbus RTU and MIL-STD-1553 implement parity in firmware without a dedicated parity unit. Each bit-processing loop has even- and odd-parity paths; a received or transmitted one changes the active path. This keeps the CRC hardware available while supporting parity generation and checking. The tests exercise errors in both parity states.

### Receiver synchronization

Receiver timing tolerance was measured by sweeping the transmitter rate. UART tolerated approximately ±5%; CAN received the tested frames within approximately ±0.7% (±0.4% without stuff bits); USB low-speed tolerated approximately +0.4%/−0.6%; and MIL-STD-1553 approximately −0.25%/+0.7%.

CAN and USB use the synchronized input path and limited re-anchoring rather than continuous clock recovery. The resulting tolerance is dependent on frame structure and transition patterns.

## FPGA hardware testing

The FPGA implementation uses four engines with inferred block RAM instruction memories. Tests were performed at 48 MHz using a Basys3 and Analog Discovery 3, with host commands supplied through [fpga/tests/](../fpga/tests/).

| Demonstration | Hardware result | Capture |
|---|---|---|
| Four-engine execution | Simultaneous 6, 12, 24, 48 kHz waveforms | [Four engines](../fpga/results/screenshots/four_engines.png) |
| UART | 38,400-baud transmit and receive | [UART](../fpga/results/screenshots/uart_tx.png) |
| SPI | Mode-0 transmit and receive at approximately 100 kHz | [SPI](../fpga/results/screenshots/spi_tx.png) |
| I²C | Master transmit and inter-engine ACK | [I²C](../fpga/results/screenshots/i2c_tx.png) |
| 1-Wire | Reset/write timing and presence detection | [1-Wire](../fpga/results/screenshots/onewire_tx.png) |
| Manchester | Encoded transmit and receive loopback | [Manchester](../fpga/results/screenshots/manchester_tx.png) |
| NRZI | Hardware encode/decode loopback | SPI readback |
| PWM | Approximately 12 kHz, 25% and 75% duty cycles | [25%](../fpga/results/screenshots/pwm_25.png), [75%](../fpga/results/screenshots/pwm_75.png) |

The hardware tests also exercised SPI-based firmware loading, independent engine control, configuration readback, pin routing, and FIFO transfers. The test programs include both protocol demonstrations and general-purpose waveform generation.

## Implementation status

The final ASIC implements four engines and four IHP 256×16 SRAM macros in the Tiny Tapeout 8×4 SG13CMOS5L configuration. The GDS workflow, Tiny Tapeout precheck, and all 36 gate-level tests passed. The FPGA implementation also meets the 48 MHz timing target.

Detailed verification methodology, ASIC area/timing results, FPGA measurements, and captured waveforms are recorded in [verification.md](verification.md). Instruction encodings and host SPI programming are described in [isa.md](isa.md), with the complete design analysis in [report.md](report.md).