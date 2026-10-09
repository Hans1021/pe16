# Protocol firmware

All programs are in `fw/` and are tested in `test/test.py`, `test/test_protocols.py` and `test/test_cps.py` through
the SPI host port, against pin-level reference models. Timing constants are written as times
(`{us(70)}`, `{bit(500000)}`) and converted to cycles by the assembler at the design clock,
48 MHz (`isa/pe16.py: CLOCK_HZ`); the tests derive their expectations from the same constant. "Limits" lists what the firmware does not do; none of it is tested on silicon.

| Firmware | Protocol | Pins (uio) | Config (reg 1 fields) | Test evidence | Limits |
|---|---|---|---|---|---|
| `uart_tx` / `uart_rx` | UART 8N1 | 0 TX / 1 RX | osr_lsb / isr_lsb | 4 + 3 bytes; TX edges on exact 16-clock multiples | baud set by DLY constants |
| `spi_ctl` | SPI controller, mode 0 | 2 SCK 3 MOSI 4 MISO 5 CS | MSB first | 3 bytes each way vs target model | mode 0 only |
| `spi_tgt` | SPI target, mode 0 | same, inputs/outputs swapped | MSB first | 3 bytes each way vs controller model | SCK half period > ~5 clocks |
| `i2c_ctl` | I2C controller, fast mode | 0 SCL 1 SDA (open drain) | MSB first | write, 2-byte read with ACK/NACK, NACK from absent device, clock stretch; SCL measured: low >= 1.5 us, high >= 1.1 us, <= 384 kHz | 7-bit addresses, no multi-master arbitration |
| `i2c_tgt` | I2C target @ 0x42 | 0 SCL 1 SDA | MSB first | write byte, read byte, other address ignored | one data byte per transaction |
| `jtag` | JTAG TMS/TDI/TDO sequencer | 0 TCK 1 TMS 2 TDI 3 TDO | MSB first | IEEE 1149.1 TAP model: IDCODE read, ends in Run-Test/Idle | none known; host builds the TMS path |
| `swd` | SWD read | 0 SWCLK 1 SWDIO | LSB first | line reset, DPIDR read, ACK OK, parity | reads only; no WAIT/FAULT retry |
| `ps2_rx` | PS/2 device-to-host | 0 CLK 1 DATA | LSB first | 2 scan codes, 16.7 kHz device clock | receive only |
| `onewire` | 1-Wire controller, standard speed | 0 DQ (open drain) | LSB first | reset + presence, Read ROM (0x33), 2 ROM bytes; every host-driven low pulse measured inside the standard-speed limits (reset 480.1 us) | standard speed only |
| `ws2812` | WS2812 LED stream | 0 | MSB first | 48 bits: high 20 or 39 clocks (417 or 813 ns), period 60 clocks (1.25 us) exactly | host must keep the FIFO fed; an empty FIFO is a latch |
| `dmx` | DMX512 transmitter | 0 | LSB first | break 90 us, MAB 12 us, 5 slots 8N2 at 250 kbaud | transmit only |
| `lin` | LIN master frame | 0 | LSB first | break 13 bits, delimiter 1 bit, sync/PID/data/checksum at 19200 | host computes PID and checksum; no response receive |
| `mdio` | MDIO Clause 22 | 0 MDC 1 MDIO | MSB first | write then read-back, PHY ID read; MDC measured: high and low 208 ns, period 417 ns (limits 160/160/400 ns) | Clause 45 not supported |
| `can_tx` | CAN 2.0A/B transmit, 500 kb/s | 6 TXD | poly 0x4599, crcw 14, stuff_n 5 | 12 frames bit-exact vs ISO 11898-1 builder | no arbitration monitoring, no ACK check |
| `can_rx` | CAN 2.0A/B receive + ACK, 500 kb/s | 7 RXD, 6 TXD | isr MSB first, same CRC/stuffing | 5 good frames (incl. extended, 8-byte) ACKed; corrupted frame rejected, not ACKed | hard sync on SOF and stuff bits: tolerance +-0.7% on test frames, +-0.4% without stuff bits; no error frames |
| `usb_tx` | USB low-speed packet transmit, 1.5 Mb/s | 2 D-, 3 D+ | nrzi, diff, stuff_n 6 ones, CRC16 or CRC5 | DATA0 (8 bytes), empty DATA1, SETUP and IN tokens, line states vs reference encoder | packets only, no enumeration |
| `usb_rx` | USB low-speed packet receive | 2 D-, 3 D+ | same | DATA0 accepted, corrupted DATA0 rejected, token accepted | only stuff-bit hard sync after SYNC: tolerance +0.4/-0.6% against the +-1.5% USB expects; host supplies the CRC residue constant |
| `eth_tx` | 10BASE-T framing, Manchester + link pulses | 0 TD+, 1 TD- | LSB first | 208-bit frame decoded; NLP 5 clocks (104 ns) wide, 16.000 ms apart | 2 Mb/s, not 10 Mb/s (loop needs 9 cycles per half bit, 10BASE-T allows 2.4); host supplies preamble and FCS; no receive |
| `modbus_tx` | Modbus RTU transmit (8E1, 115200) | 0 TX, 2 RS-485 DE | poly 0x8005 crcw 15, LSB first | two frames: bytes, even parity and stop bits decoded; CRC appended by the engine (spec example `11 03 00 6B 00 03` ends `76 87`); silence between frames 1759 us (t3.5 = 1750 us); DE covers each frame | even parity only (8E1); host gives byte count + 1 |
| `modbus_rx` | Modbus RTU receive + frame end + CRC check | 1 RX | same | valid frame (status 0), corrupted CRC (status 1), parity errors on even- and odd-weight bytes (status 2, rest of frame discarded), then recovery | t1.5 inter-character limit not checked; host supplies the t3.5 poll count |
| `mil1553_tx` | MIL-STD-1553 words, 1 Mb/s Manchester II | 0 TX+, 1 TX- | MSB first | 4 back-to-back words (command sync + 3 data syncs): 160 half-bits match the reference encoder, every line edge on the 500 ns grid, idle afterwards | word sequencing only; bus controller/remote terminal logic (response times, status bits) is host work; needs a 1553 transceiver |
| `mil1553_rx` | MIL-STD-1553 word receive | 2 RX+, 3 RX- | MSB first | command + 2 contiguous data words decoded; parity errors in both parity states (0x20); missing mid-bit transition on 0 and 1 bits in both parity states (0x10); recovery | no clock recovery within a word (tolerance -0.25/+0.7%, bit rate is specified at +-0.1%); expects an idle bus between messages |

The RX FIFO is 4 words deep and the engine stalls on `PUSH` when it is full. Protocols that
return many words (JTAG, CAN RX, USB RX) need the host to drain the FIFO during the transfer.
The tests do this with the SPI port at clk/8.

Timing was measured against the protocol limits for I2C, MDIO, 1-Wire, WS2812, DMX512, LIN, UART,
Manchester, Modbus RTU (t3.5 silence) and MIL-STD-1553 (500 ns edge grid).

Parity without a parity unit: Modbus RTU (even) and MIL-STD-1553 (odd) track parity in the program
counter. Each bit loop exists twice, for "even so far" and "odd so far", and a 1 bit jumps to the
other copy, so the CRC unit stays free for the frame CRC. The receivers check parity the same way,
and the tests inject errors on both copies. These checks found and fixed two firmware defects: I2C SCL low time of 1.0 us
(fast mode needs 1.3 us) and a 1-Wire reset of 479.9 us (needs 480 us).

## Engine count and instruction store

| Build | Instruction store | Engines | Std-cell + macro area | Tests |
|---|---|---|---|---|
| default | IHP 256x16 SRAM macro per engine | 4 | 0.254 mm2 (28% of die) | 21/21 |
| `-DPE_FLOP_IMEM` | 128x16 flip-flops per engine | 2 | 0.412 mm2 (45% of die) | 21/21 |

The SRAM build needs a LibreLane power step to reach the macros' Metal4 power pins, which the
standard Tiny Tapeout power grid does not do; another entrant (kdp1965) has published one. The
flip-flop build uses the standard flow unchanged. Four engines with flip-flop stores take 89%
of the die and are not a viable option.
