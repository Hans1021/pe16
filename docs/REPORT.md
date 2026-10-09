# PE16 Protocol Engine: Technical Report

Entry for the Jane Street protocol emulator ASIC competition (IHP SG13CMOS5L 130 nm via Tiny
Tapeout, 6x4 tiles, submission deadline 18 January 2027).

This report explains what the chip does, how the RTL implements it, how each protocol is
built in firmware, and what the verification does and does not establish. Every number here
comes from a check in the repository. `VERIFICATION.md` lists the raw results, and
`logs/verify.log` holds the output of the last full regression. Nothing has been placed,
routed or fabricated yet.

## Contents

1. Summary
2. Challenge constraints
3. System architecture
4. Engine RTL (`src/pe_core.v`)
5. Top level (`src/project.v`) and instruction memory (`src/pe_imem.v`)
6. Instruction set and assembler
7. Firmware techniques
8. Protocols, one section per protocol
9. Verification
10. Synthesis and timing
11. Known issues and open items
12. Reproducing the results

---

## 1. Summary

PE16 is a small multi-core protocol processor. Each core, called an engine, is a 16-bit
single-issue processor whose instruction set is built around pin timing. It has a deadline
delay instruction for cycle-exact bit periods, a configurable CRC unit, and a bit-stuffing
unit with NRZI and differential line coding. Protocols are firmware. The default build has
4 engines that run independently, so 4 protocols can run at the same time. Any engine can be
reloaded with a different program over a SPI host port at any time.

| Item | Value |
|---|---|
| Engines | 4 (SRAM build) or 2 (flip-flop fallback build) |
| Program memory per engine | 256 x 16 bits (IHP SRAM macro) or 128 x 16 bits (flip-flops) |
| System clock | 48 MHz (not yet confirmed by post-layout timing) |
| Protocol pins | 8 bidirectional pins `uio[7:0]`, shared by all engines |
| Host interface | SPI mode 0 on `ui_in[2:0]` and `uo_out[0]` |
| Firmware programs | 23, covering 16 protocols |
| Top-level tests | 25, all passing in all three builds |
| Lean theorems | 8, no `sorry`, standard axioms only |
| Mutation tests | 20 RTL mutants (19 detected, 1 proved equivalent), 8 Lean-model mutants (8 detected), 37 firmware mutants (28 detected) |
| Area (4 engines) | 0.142 mm2 standard cells + 4 x 0.028 mm2 SRAM = 0.254 mm2, 28% of the 6x4 die |

## 2. Challenge constraints

**Process and area.** IHP SG13CMOS5L through Tiny Tapeout. The competition fixes the tile
allocation at 6x4. The Tiny Tapeout support tools give this outline as 1289.28 x 710.64 um
(0.916 mm2). The competition page quotes about 0.7 mm2 of nominal tile area and about 1K
logic cells per tile. An 8x4 option was under discussion at the time of writing and was not
in the CMOS5L support tools.

**Pins.** The competition page does not set a pin count. The constraint comes from the
required Tiny Tapeout template, which gives every design the same port list:

| Port | Width | Direction | PE16 use |
|---|---|---|---|
| `ui_in` | 8 | input | bits 0 to 2: SPI host port (SCK, CS_N, MOSI). Bits 3 to 7 unused. |
| `uo_out` | 8 | output | bit 0: MISO. Bits 1 to 3: halt, timing overrun, run (any engine). Bits 4 to 7 unused. |
| `uio_in`, `uio_out`, `uio_oe` | 8 | bidirectional, per-pin enable | the 8 protocol pins |
| `clk`, `rst_n`, `ena` | 1 each | input | clock, reset, enable |

Only the `uio` pins can be both driven and read with a per-pin output enable. Open-drain
buses (I2C, 1-Wire) and bidirectional lines (SWDIO, MDIO) need that, so all protocols run on
`uio`. The 5 unused inputs and 4 unused outputs could extend the protocol pin budget to 17
(section 11).

**I/O speed.** Every Tiny Tapeout pin passes through the shared chip's I/O multiplexer. The
competition page gives no maximum toggle rate and this work has not measured one. The
fastest protocol signals used are the 3 MHz SPI, JTAG and SWD clocks and the 1.5 Mb/s USB
low-speed line.

## 3. System architecture

```
                  +-----------------------------------------------------------+
 host MCU         |  tt_um_pe16                                               |
 (e.g. RP2040)    |                                                           |
  SCK  -> ui_in[0]|--+                                                        |
  CS_N -> ui_in[1]|--+-> 2-flop sync -> SPI command decoder                   |
  MOSI -> ui_in[2]|--+        |  program load, config, TX push,               |
  MISO <- uo_out[0]<----------+  RX pop, status, counts, run/reset            |
                  |           v                                               |
                  |   +--------+ +--------+ +--------+ +--------+             |
                  |   |engine 0| |engine 1| |engine 2| |engine 3|             |
                  |   | + SRAM | | + SRAM | | + SRAM | | + SRAM |             |
                  |   +--------+ +--------+ +--------+ +--------+             |
                  |        pout/poe ORed across engines, pins read by all      |
                  |   uio_in[7:0] -> 2-flop sync -> every engine               |
                  +-------------------------|---------------------------------+
                                            v
                                     uio[7:0]  protocol pins
                                     (to the target, via a transceiver where needed)
```

**Data path.** There is no parallel data bus. Protocol data moves through the host port.

Transmit: host SPI -> engine TX FIFO (4 x 16 bits) -> `PULL` into the output shift register
`osr` -> `OUT` onto a `uio` pin.

Receive: `uio` pin -> `IN` into the input shift register `isr` -> `PUSH` into the engine RX FIFO
(4 x 16 bits) -> host SPI read on MISO.

Each engine has its own FIFOs. The host addresses an engine with the top two bits of each
command byte. The host port accepts SCK up to 1/8 of the system clock (6 MHz at 48 MHz). An
engine stalls on `PULL` when its TX FIFO is empty and on `PUSH` when its RX FIFO is full. So a
receive-heavy protocol (JTAG, CAN receive, USB receive, Modbus receive, 1553 receive) needs the
host to read the RX FIFO while the transfer is running. The tests do this.

## 4. Engine RTL (`src/pe_core.v`, 227 lines)

### 4.1 Architectural state

| Register | Width | Purpose |
|---|---|---|
| `pc` | 8 | program counter |
| `x`, `y` | 16 | loop counters and general values |
| `osr` | 16 | output shift register, loaded by `PULL` or `MOV` |
| `isr` | 16 | input shift register, drained by `PUSH` |
| `crc` | 16 | CRC register (width and polynomial set by the host) |
| `pout`, `poe` | 8 | pin output values and output enables |
| `b` | 1 | last bit read by `IN` or placed by `OUT` |
| `s` | 1 | last `OUT stuff` or `IN destuff` was a stuff bit |
| `ov` | 1 | sticky timing overrun: a `DLY` deadline was missed |
| `since` | 16 | cycles since the last `DLY` retired or `SET CTL sync` (saturating) |
| `last`, `runl` | 1, 3 | bit-stuffing unit: last line bit and current run length |
| `rxl` | 1 | last sampled line level, for NRZI decoding |
| `halt` | 1 | engine stopped by `HALT` |
| TX, RX FIFO | 4 x 16 each | host interface |

Host configuration per engine (register 0: CRC polynomial, register 1:
`{diff, nrzi, stuff_ones, stuff_n[2:0], isr_lsb, osr_lsb, crcw[3:0]}`) selects CRC width,
shift directions, stuffing rule and line coding.

### 4.2 Execution

Every instruction retires in one cycle unless it stalls. `WAIT` stalls until a pin has a given
level. `DLY` stalls until its deadline. `PULL` stalls on an empty TX FIFO, `PUSH` on a full RX
FIFO, and `HALT` stops the engine. There is no pipeline hazard logic because there is no
pipeline beyond fetch.

**Fetch loop.** The instruction memory is a synchronous-read RAM. The combinational next-PC
(`pc_next`: branch target, same PC on a stall, else PC + 1) drives the RAM address directly, and
the RAM output is the instruction for the next cycle. This gives one instruction per cycle
with no fetch bubble. The path RAM output -> decode -> `pc_next` -> RAM address is the
design's critical timing loop (section 10).

**Deadline timer (`DLY`).** `since` counts cycles since the last `DLY` retired. `DLY k` stalls
until `since + 1 >= k`, then clears `since`. If `since + 1 > k` on arrival, the deadline has
already passed and `ov` is set. Consecutive `DLY k` instructions therefore retire exactly k
cycles apart, whatever path the code between them took, as long as that code takes fewer
than k cycles. Firmware places each pin change on the cycle right after a `DLY`, so edges fall
on an exact grid. `SET CTL sync` re-anchors the timer to an external event, for example after
a `WAIT` for a start bit. Lean theorems `dly_retire`, `dly_period` and `since_incr` state these
properties about the Lean model (section 9.3).

**CRC unit.** One serial CRC step per `OUT crc`, `OUT stuff`, `IN crc` or `IN destuff`:
`fb = crc[w-1] ^ d`, `crc = (crc << 1) & mask ^ (fb ? poly : 0)`, with width 1 to 16 set by the
host. Width 1 with polynomial 1 gives a parity bit. Reflected CRCs (USB, Modbus) are computed
by feeding bits in line order, then bit-reversing the register with `MOV @`. `MOV ^`
left-justifies a CRC of any width and pads the freed low bits with ones. `JMP cz` tests for a
zero remainder.

**Bit-stuffing unit.** `stuff_n` sets the run length (0 disables stuffing). In identical-bit mode
(CAN) the unit counts equal consecutive bits and inserts the complement. In ones mode (USB,
HDLC) it counts ones and inserts a 0. `OUT stuff` either places a stuff bit (without consuming
`osr` and without feeding the CRC, and sets `s`) or the next data bit. `IN destuff` discards a
stuff bit and sets `s`. `JMP s` repeats the bit slot, and `JMP due` tests whether a stuff bit is
due, for a trailing stuff bit at the end of a field.

**Line coding.** With `nrzi` set, `OUT` drives NRZI (data 0 toggles the line) and `IN` decodes
relative to `rxl`. With `diff` set, `OUT` also drives pin i+1 with the complement (USB D+ and
D-). `IN peek` always returns the raw pin level and leaves the NRZI state alone, which USB
needs to detect SE0.

**FIFOs.** The host side of each FIFO works while the engine is stopped. Engine and host both
act on start-of-cycle FIFO counts. The Python and Lean models use the same ordering, which
the differential tests confirm.

## 5. Top level and instruction memory

### 5.1 Host port command set (`src/project.v`)

Command byte = `{engine[1:0], code[5:0]}`, MSB first, SPI mode 0, SCK at most clk/8.

| Code | Command | Payload |
|---|---|---|
| 1 | program write | address byte, then 16-bit words (auto-increment). Ignored while the engine runs. |
| 2 | configuration write | register byte, 16-bit value |
| 3 | TX push | 16-bit words |
| 4 | RX pop | reads 16-bit words (0 when empty) |
| 5 | status | reads `{pc, run, halt, ov, 00000}` |
| 6 | FIFO counts | reads `{rx_count, tx_count}` |
| 7 | control | one byte: bit 0 run, bit 1 engine reset |

SCK, CS_N, MOSI and all 8 `uio` inputs pass through 2-flop synchronisers. Pin outputs and
enables are ORed across engines, so engines that run at the same time must use separate pins
(section 11, known issue 1).

### 5.2 Instruction memory (`src/pe_imem.v`)

| Build | Store | Engines | Area share | Flow risk |
|---|---|---|---|---|
| default | IHP `RM_IHPSG13_1P_256x16` SRAM macro per engine | 4 | 28% | the standard Tiny Tapeout power grid cannot reach the macro's Metal4 power pins. A custom LibreLane power step is needed. Another entrant has published one. |
| `-DPE_FLOP_IMEM` | 128 x 16 flip-flops per engine | 2 | 45% | standard flow unchanged |

Four engines with flip-flop stores would take 89% of the die and are not an option. The
largest program is 96 instructions, so all programs fit either build. Simulation uses a
behavioural memory with the same read timing, and the full test suite also passes with the
IHP SRAM functional model.

## 6. Instruction set and assembler

16-bit instructions, opcode in bits [15:13]. Full encoding in `docs/isa.md`.

| Op | Mnemonics | Function |
|---|---|---|
| 0 | `JMP cond, addr` | conditions: always, `x0`, `x--`, `y0`, `y--`, `b`, `s`, `cz`, `x!=y`, `due`, `txe` |
| 1 | `WAIT pol, pin` | stall until the pin has level `pol` |
| 2 | `DLY k` / `DLY x` | deadline delay (k up to 4095, or the value in `x`) |
| 3 | `OUT mode, pin` | raw, crc, stuff, oe: shift one `osr` bit onto a pin or its output enable |
| 4 | `IN mode, pin` | raw, crc, destuff, peek: sample one pin bit into `isr` or `b` |
| 5 | `SET` | one pin value or enable, all 8 pins or enables, `x`, `y` (10-bit immediate), control flags (`crc0`, `crc1`, `stf`, `isr0`, `ov0`, `sync`) |
| 6 | `MOV dst, src` | between `x`, `y`, `osr`, `isr`, `crc`, pins, enables, with optional invert `~`, CRC align `^`, bit reverse `@` |
| 7 | `PULL`, `PUSH`, `HALT`, `NOP` | FIFO access, stop |

**Assembler (`isa/pe16.py`).** Two-pass, with labels, `.equ` constants, and `{expr}` expressions
evaluated at the design clock: `ns(t)`, `us(t)`, `ms(t)` round up to whole cycles, and
`bit(baud)` rounds to the nearest cycle. Firmware timing is written as time (for example
`dly {us(70)}` or `.equ T {bit(500000)}`), so changing `CLOCK_HZ` retimes every program. The
assembler rejects `DLY` values outside 1 to 4095 and `SET` immediates outside 0 to 1023. The
range check caught two out-of-range constants during development.

## 7. Firmware techniques

**Deadline-timed edges.** Each timed pin change sits on the cycle after a `DLY`. Branches, word
pulls and counters in between cost no timing error while they fit inside the delay.

**Parity in the program counter.** The ISA has no parity unit, and Modbus RTU and MIL-STD-1553
need parity while the CRC unit is busy or absent. Each bit loop exists in two copies,
"even so far" and "odd so far". A 1 bit jumps to the other copy. At the parity bit, the
current copy decides the value. Receivers check parity the same way.

**Open drain.** The output value stays 0 and firmware toggles only the output enable. Enabled
pulls the line low. Disabled lets the external pull-up raise it.

**Scratch pin.** Some programs test the next `osr` bit by shifting it onto pin 7 with its output
disabled (`OUT raw 7`, which also sets `b`), then branching on `b`. Nothing is driven. See
section 11 for the multi-engine consequence.

**Host drain.** Receivers push partial results as they go, and the host reads them while the
transfer runs.

## 8. Protocols

Each entry lists pins (`uio`), rate at 48 MHz, engine configuration, host interface, how the
firmware works, test evidence, and limits. "Reference model" means pin-level Python code in
the testbench, written from the protocol specification. It never reads engine state.

### 8.1 UART (`uart_tx`, `uart_rx`)

- **Pins:** 0 TX (out), 1 RX (in).
- **Rate:** 16 clocks per bit as shipped (3 Mbaud). Set by the `DLY` constant, for example `{bit(115200)}`.
- **Config:** `osr_lsb` for TX, `isr_lsb` for RX. 8N1.
- **Host:** TX takes one byte per word. RX returns the byte in bits [15:8].
- **Firmware:** TX pulls a byte, re-anchors the timer, drives the start bit, shifts 8 bits on deadline edges, then the stop bit. RX waits for a start edge, re-anchors, waits half a bit, then samples every bit time at the bit centre.
- **Tests:** `uart_tx` (4 bytes decoded by a centre-sampling reference receiver, framing checked), `uart_tx_period` (every edge on the 16-clock grid), `uart_rx` (7 bytes from a reference transmitter at +-5% bit-rate error and random phase). The receiver compensates the 3-cycle edge-detection latency. Measured tolerance: +-5%, failing at +-6%.
- **Limits:** no parity option in these programs (Modbus shows how to add it). No break detection.

### 8.2 SPI controller and target (`spi_ctl`, `spi_tgt`)

- **Pins:** 2 SCK, 3 MOSI, 4 MISO, 5 CS.
- **Rate:** controller SCK 3 MHz. The target follows an external SCK. Tested at 1.5 MHz, estimated limit about 4 MHz (SCK half period must exceed about 5 system clocks).
- **Config:** MSB first both ways, mode 0.
- **Host:** transmit byte in bits [15:8], received byte in bits [7:0].
- **Firmware:** the controller drops CS, then per bit sets MOSI, raises SCK on a deadline, samples MISO, and lowers SCK. The target waits for CS, presents its first bit, then samples MOSI on each SCK rise and shifts MISO on each fall.
- **Tests:** `spi_controller` against a mode-0 target model, `spi_target` against a controller model. 3 bytes each way, both directions checked.
- **Limits:** mode 0 only.

### 8.3 I2C controller (`i2c_ctl`)

- **Pins:** 0 SCL, 1 SDA (open drain), 7 scratch.
- **Rate:** fast mode. SCL low 1.5 us and high 1.0 us plus stretching.
- **Config:** MSB first.
- **Host:** one word per byte, `{S, P, R, A, 0000, data}`: START before, STOP after, read, NACK after read. Returns the ACK bit for writes and the byte for reads.
- **Firmware:** START and STOP sequences, 8 data bits plus ACK per word, `WAIT` on SCL high for clock stretching, repeated START supported.
- **Tests:** `i2c_controller` against a target model at address 0x42 with a 300-clock stretch: write, two-byte read with ACK then NACK, and an absent address that must NACK. START and STOP counts checked. SCL timing measured against the fast-mode limits: minimum low 1.5 us (limit 1.3), minimum high 1.1 us (limit 0.6), maximum 384 kHz (limit 400).
- **Defect found:** the first version had 1.0 us low phases in the read and ACK paths (475 kHz). The timing check caught it, and a mutant restoring it is detected.
- **Limits:** 7-bit addresses, no multi-controller arbitration.

### 8.4 I2C target (`i2c_tgt`)

- **Pins:** 0 SCL, 1 SDA (open drain).
- **Address:** 0x42, fixed in firmware.
- **Host:** reply byte in bits [15:8]. The received byte is pushed.
- **Firmware:** detects START as SDA falling while SCL is high, shifts 7 address bits, compares with `JMP x!=y`, ACKs, then receives or sends one byte and waits for STOP.
- **Tests:** `i2c_target` with a bit-banged controller: write byte, read byte with NACK, other address ignored.
- **Limits:** one data byte per transaction, no clock stretching.

### 8.5 JTAG (`jtag`)

- **Pins:** 0 TCK, 1 TMS, 2 TDI (out), 3 TDO (in).
- **Rate:** TCK 3 MHz.
- **Host:** bit count minus 1, then words of 8 {TMS, TDI} pairs. The engine returns TDO bits, 8 per word.
- **Firmware:** a general TMS/TDI/TDO sequencer. The host builds any TAP path.
- **Tests:** `jtag_idcode` against an IEEE 1149.1 TAP model (16-state controller, 4-bit IR): reset, move to Shift-DR, read 32 bits of IDCODE 0x4BA00477, end in Run-Test/Idle.
- **Defect found in the test:** the first version read the RX FIFO only at the end, but a 43-bit scan fills more than 4 words. The engine stalled correctly and the host now drains during the scan.

### 8.6 SWD (`swd`)

- **Pins:** 0 SWCLK, 1 SWDIO (bidirectional).
- **Rate:** 3 MHz.
- **Host:** line-reset length (0 = none), then the request byte. The engine returns 3 words holding ACK, 32 data bits and parity.
- **Firmware:** optional line reset, 2 idle cycles, 8 request bits, turnaround, 36 read bits, turnaround back, idle.
- **Tests:** `swd_dpidr` against a target model that checks the request's start, stop, park and parity bits and returns DPIDR 0x2BA01477. ACK = OK and data parity are checked.
- **Limits:** reads only, no WAIT or FAULT retry.

### 8.7 MDIO, Clause 22 (`mdio`)

- **Pins:** 0 MDC, 1 MDIO (bidirectional), 7 scratch.
- **Rate:** MDC period 417 ns (2.4 MHz, limit 2.5 MHz).
- **Host:** header word `{01, OP, PHYAD, REGAD, TA}`, plus a data word for writes. Reads return the register value.
- **Firmware:** 32-bit preamble, header, then either TA plus 16 written bits, or releasing MDIO for TA and sampling 16 bits before each MDC rise.
- **Tests:** `mdio` against a PHY model at address 3: register write, read-back, and PHY ID read. The model checks preamble, start code and write TA. MDC measured: high and low 208 ns (limit 160 ns), period 417 ns (limit 400 ns).
- **Limits:** Clause 45 not supported.

### 8.8 PS/2 receive (`ps2_rx`)

- **Pins:** 0 CLK, 1 DATA (driven by the device).
- **Rate:** follows the device clock. Tested at 20 kHz.
- **Host:** 11-bit frame in bits [15:5]. The host checks parity.
- **Firmware:** 8 instructions: wait for CLK falling, sample DATA, repeat 11 times, push.
- **Tests:** `ps2_receive` with a device model sending two scan codes with odd parity.
- **Limits:** receive only. No host-to-device commands.

### 8.9 1-Wire (`onewire`)

- **Pins:** 0 DQ (open drain), 7 scratch.
- **Rate:** standard speed.
- **Host:** `{data, op}` with op 0 reset, 1 write byte, 2 read byte. Returns the presence bit or the byte.
- **Firmware:** reset low 480 us, presence sampled at 70 us, write slots of 60 us (0) or 6 us (1), read slots with the sample at 13 us. The timer is re-anchored at every falling edge.
- **Tests:** `onewire` against a slave model: reset, presence, Read ROM command 0x33, two ROM bytes returned. Every host-driven low pulse is checked against the standard-speed windows, and recovery is at least 1 us.
- **Defect found:** the reset pulse was 479.9 us because the deadline chain started a few cycles before the falling edge. Fixed by re-anchoring. A mutant removing the fix is detected.
- **Limits:** no overdrive speed, no search ROM.

### 8.10 WS2812 (`ws2812`)

- **Pins:** 0 DIN, 7 scratch.
- **Rate:** 800 kHz (1.25 us bit).
- **Host:** GRB bit stream, 16 bits per word. An empty FIFO holds the line low, which latches the LEDs.
- **Firmware:** the high time depends on the current bit, and the next `DLY x` completes the previous bit's low time, so the period is exact.
- **Tests:** `ws2812` with 48 bits (2 LEDs): high 20 or 39 clocks (417 or 813 ns), every rising edge exactly 60 clocks (1.25 us) apart, bits decoded.
- **Limits:** the host must keep the FIFO fed.

### 8.11 DMX512 transmit (`dmx`)

- **Pins:** 0 TX. **Rate:** 250 kbaud, 8N2.
- **Host:** slot count minus 1, then one byte per word, with slot 0 as the start code.
- **Firmware:** break 90 us, mark-after-break 12 us, then slots.
- **Tests:** `dmx512`: break 90 us (limit 88), MAB 12 us (limit 8), 5 slots decoded.
- **Limits:** transmit only.

### 8.12 LIN master frame (`lin`)

- **Pins:** 0 TXD. **Rate:** 19.2 kbaud, 8N1.
- **Host:** byte count minus 1, then sync 0x55, PID, data and checksum. The host computes PID and checksum.
- **Firmware:** 13-bit break, 1-bit delimiter, then bytes.
- **Tests:** `lin`: break 13 bits, delimiter 1 bit, bytes decoded, and PID parity and enhanced checksum verified by an independent reference.
- **Defect found:** break and delimiter were each one clock off. Fixed.
- **Limits:** no slave response reception.

### 8.13 CAN 2.0A/B (`can_tx`, `can_rx`)

- **Pins:** 6 TXD, 7 RXD.
- **Rate:** 500 kb/s (96 clocks per bit).
- **Config:** CRC-15 polynomial 0x4599, width 15, stuffing after 5 identical bits.
- **Host (TX):** bit count minus 1, then SOF through data bits. The engine adds stuffing, CRC-15, the delimiter, ACK slot and EOF.
- **Host (RX):** header, DLC, data bytes, and a status word (0 = CRC good).
- **Firmware (TX):** `OUT stuff` per bit with `JMP s` to repeat the slot after a stuff bit. The CRC is left-justified with `MOV ^` so the trailing stuff bit and delimiter come from the same loop.
- **Firmware (RX):** hard sync on SOF, `IN destuff` per bit, branches on IDE for extended frames, reads DLC then the data bytes, checks the CRC remainder, and drives a dominant ACK only if the CRC is good.
- **Tests:** `can_tx`: 12 frames (2 extended, all-zero and all-one data) bit-exact against an ISO 11898-1 frame builder, then decoded and CRC-checked, with no timing overrun. `can_rx`: 5 good frames decoded and ACKed, 1 corrupted frame rejected and not ACKed. The CRC-15 reference returns the catalogue check value 0x059E [20].
- **Limits:** the receiver hard-syncs on SOF and on stuff bits, so it needs bit rates matched within about 0.4% in the worst case and 0.7% on the test frames. No error frames. The transmitter does not monitor arbitration or check ACK.

### 8.14 USB low speed (`usb_tx`, `usb_rx`)

- **Pins:** 2 D-, 3 D+. **Rate:** 1.5 Mb/s (32 clocks per bit). This is why the clock is 48 MHz.
- **Config:** NRZI, differential output, stuffing after 6 ones. CRC-16 (0x8005) for data packets, CRC-5 (0x05) for tokens.
- **Host (TX):** {PID, SYNC}, payload bit count, payload, CRC length. The engine sends SYNC, PID, payload, the inverted and reversed CRC (`MOV ~crc^@`), a final stuff bit if due (`JMP due`), then SE0 SE0 J.
- **Host (RX):** first the CRC residue constant, then the engine returns the SYNC and PID word, data words, the bit count and a status word.
- **Firmware (RX):** detects SE0 with raw `IN peek` on both lines and compares the CRC remainder with `JMP x!=y`.
- **Tests:** `usb_tx`: DATA0 with an 8-byte SETUP payload, empty DATA1, SETUP and IN tokens. Line states compared with a reference encoder. `usb_rx`: DATA0 accepted, corrupted DATA0 rejected, token accepted. The residue constants 0x800D and 0x0C were confirmed by these tests. The reference CRCs return the published check values 0xB4C8 and 0x19.
- **Limits:** packets only, no enumeration or handshake timing. No clock recovery after SYNC beyond stuff-bit hard sync, so the measured tolerance is +0.4/-0.6% against the +-1.5% USB expects. Needs the 48 MHz clock.

### 8.15 10BASE-T-style framing (`eth_tx`)

- **Pins:** 0 TD+, 1 TD-, 7 scratch.
- **Rate:** 2 Mb/s Manchester, not 10 Mb/s.
- **Host:** bit count minus 1, then the whole frame including preamble, SFD and FCS.
- **Firmware:** Manchester coding on deadline edges, TP_IDL at frame end, a normal link pulse every 16 ms while idle, and polling of the TX FIFO with `JMP txe`.
- **Tests:** `eth_manchester`: 208-bit frame decoded, link pulse 104 ns wide and 16.000 ms apart, no timing overrun.
- **Defect found:** with a half-bit of 8 clocks, the word-boundary path (9 cycles) missed its deadline. The half-bit was widened to 12 clocks.
- **Limits:** real 10BASE-T needs about 2.4 clocks per half-bit at 48 MHz, and this loop needs 9, so the architecture cannot meet 10 Mb/s. No receive. The CRC unit is 16 bits wide, so the host supplies the CRC-32.

### 8.16 Modbus RTU (`modbus_tx`, `modbus_rx`)

- **Pins:** 0 TX, 2 RS-485 driver enable (TX side). 1 RX (RX side).
- **Rate:** 115200 baud, 8E1. End of frame after 3.5 characters of silence (1.75 ms above 19200 baud).
- **Config:** CRC-16/MODBUS: polynomial 0x8005, start value 0xFFFF, fed in line order, reversed with `MOV @`.
- **Host (TX):** data byte count plus 1, then bytes. The engine appends the CRC and enforces t3.5 silence after the frame.
- **Host (RX):** the t3.5 poll count. Returns `{byte, 0xFF}` per character and a status word: 0 CRC good, 1 CRC error, 2 parity or framing error.
- **Firmware:** even parity in the program counter (section 7). The receiver detects frame end by polling the line every 16 clocks and counting silence. On a parity error it discards the rest of the frame.
- **Tests:** `modbus_tx`: two frames decoded. The engine-computed CRC for `11 03 00 6B 00 03` is `76 87`, matching the widely republished example frame [18]. Parity and stop bits checked. Silence between frames 1759 us (limit 1750 us). Driver enable covers each frame. `modbus_rx`: valid frame (status 0), corrupted CRC (1), parity errors on an even-weight and on an odd-weight byte (2), then recovery. The reference CRC returns the published check value 0x4B37.
- **Limits:** the 1.5-character inter-character limit is not checked. 8E1 only.

### 8.17 MIL-STD-1553 (`mil1553_tx`, `mil1553_rx`)

- **Pins:** TX 0 TX+, 1 TX-, 7 scratch. RX 2 RX+, 3 RX-. Both low = idle.
- **Rate:** 1 Mb/s Manchester II, 24 clocks per half-bit.
- **Host (TX):** word count minus 1, then words. The first word gets a command/status sync, the rest get data syncs, all sent back to back.
- **Host (RX):** per word, the data word and a status word: 1 command/status, 2 data, 0x10 Manchester error, 0x20 parity error, 0x30 bad sync.
- **Firmware:** odd parity in the program counter. The receiver samples each half-bit near its centre and checks the sync shape, the mid-bit transition of every bit, and parity. It decodes contiguous words and resynchronises after errors.
- **Tests:** `mil1553_tx`: 4 words, 160 half-bits identical to the reference encoder, every line edge on the 500 ns grid, idle afterwards. `mil1553_rx`: a command word plus 2 contiguous data words, parity errors on even- and odd-weight words, missing mid-bit transitions on 0 and 1 bits in both parity states, then recovery.
- **Limits:** word level only. Bus controller and remote terminal behaviour (response time, status bits) is host work. No clock recovery within a word. Needs a transformer-coupled 1553 transceiver.

### 8.18 Summary table

| Protocol | Programs | Pins | Rate | Instructions |
|---|---|---|---|---|
| UART | `uart_tx`, `uart_rx` | 0, 1 | 3 Mbaud as shipped | 13, 11 |
| SPI | `spi_ctl`, `spi_tgt` | 2 to 5 | 3 MHz / external | 19, 14 |
| I2C | `i2c_ctl`, `i2c_tgt` | 0, 1 (+7) | 400 kHz / external | 91, 50 |
| JTAG | `jtag` | 0 to 3 | 3 MHz | 23 |
| SWD | `swd` | 0, 1 | 3 MHz | 67 |
| MDIO | `mdio` | 0, 1 (+7) | 2.4 MHz | 68 |
| PS/2 | `ps2_rx` | 0, 1 | device clock | 8 |
| 1-Wire | `onewire` | 0 (+7) | standard | 48 |
| WS2812 | `ws2812` | 0 (+7) | 800 kHz | 20 |
| DMX512 | `dmx` | 0 | 250 kbaud | 26 |
| LIN | `lin` | 0 | 19.2 kbaud | 28 |
| CAN | `can_tx`, `can_rx` | 6, 7 | 500 kb/s | 27, 73 |
| USB LS | `usb_tx`, `usb_rx` | 2, 3 | 1.5 Mb/s | 42, 43 |
| 10BASE-T-style | `eth_tx` | 0, 1 (+7) | 2 Mb/s | 38 |
| Modbus RTU | `modbus_tx`, `modbus_rx` | 0, 2 / 1 | 115200 | 45, 60 |
| MIL-STD-1553 | `mil1553_tx`, `mil1553_rx` | 0, 1 (+7) / 2, 3 | 1 Mb/s | 59, 96 |

"(+7)" marks the scratch pin (section 11, known issue 1).

## 9. Verification

### 9.1 Approach

The ISA has three executable models, and the checks tie them together:

```
        random programs, 18 state fields, every cycle
 RTL  <-------------------------------------------------->  Python golden model
(pe_core.v)                                                 (isa/pe16.py)
                                                                  ^
                              random traces, every cycle          |
                     Lean model (lean/Pe16/Isa.lean)  <-----------+
                              |
                              +--> 8 theorems proved about the Lean model
```

The RTL is connected to the Lean model by simulation through the Python model, not by
proof. So the theorems are guarantees about the Lean model, and they carry over to the RTL
only as far as the random differential tests reach. Mutation testing (9.4) measures that
reach.

On top of this, the protocol tests (9.5) check firmware plus RTL against independent protocol
models at the pins, through the real SPI host port.

### 9.2 RTL vs Python model (`test/core/`)

- **Method:** 200 random programs x 600 cycles. The RTL engine and the Python model run in lock step with the same random pins and host FIFO traffic, and all 18 state fields are compared every cycle.
- **Configuration:** random CRC width and polynomial, shift directions, stuffing mode and length, NRZI and differential.
- **Program mix:** 30% of programs are biased to stress the stuffing unit. Branch conditions are drawn uniformly over all 12 encodings.
- **Result:** 0 mismatches, 80 of 80 functional coverage bins hit. The bins cover every branch condition taken and not taken, every OUT and IN mode with and without NRZI, stuff bits on both sides, every SET and MOV target with and without alignment and reversal, DLY stall, retire and overrun, WAIT stall and pass, every FIFO stall case, HALT, and a run length of 7.

### 9.3 Lean 4 model and theorems (`lean/Pe16/`)

The Lean model (`Isa.lean`) implements the same cycle step as the Python model, field for
field. A compiled Lean executable replayed 500 random Python traces x 600 cycles (300,000
cycles) with 0 mismatches. `lake build` checks the proofs. `#print axioms` shows only Lean's
standard axioms (`propext`, `Quot.sound`, `Classical.choice`) and there is no `sorry`.

| Theorem | Statement | What it means for firmware |
|---|---|---|
| `dly_retire` | at `DLY k` with `since = c < k`, the engine retires after exactly `k - c` cycles for any pin and FIFO inputs, leaving `since = 0` and `ov` unchanged | a waiting `DLY` cannot be shortened or lengthened by input activity |
| `dly_period` | with `since = m < k` on arrival, the retire is exactly `k` cycles after the previous one | bit periods are exact whatever branch path the loop body took |
| `since_incr` | every instruction except `DLY` and `SET CTL sync` adds exactly one to `since` | the cycle cost of code between deadlines is exactly its instruction count |
| `no_long_runs` | in identical-bit mode with `1 <= stuff_n <= 7`, any sequence of `OUT stuff` with any data never puts more than `stuff_n` equal bits in a row on the line | the CAN stuffing rule holds for all data, not just tested frames |
| `roundtrip` | the bits `IN destuff` accepts from the transmitted line are exactly the data bits `OUT stuff` consumed, in order, in both modes and for any `stuff_n` | stuffing and destuffing are inverses |
| `stuff1_livelock` | identical-bit mode with `stuff_n = 1` stuffs forever | a configuration hazard, documented as forbidden (also seen in the Python model) |
| `stuff_progress` | for `stuff_n >= 2` a stuff bit is never followed by another | each data bit costs at most one stuff bit, so transmission always progresses |
| `ones_stuffbit` | in USB mode the stuff bit equals `~last` whenever the mode is not changed after reset | proves the one surviving RTL mutant (9.4) is equivalent |

**Not proved:** RTL equivalence to the Lean model, anything about the CRC unit (tested
against published check values instead), FIFO behaviour, and anything about firmware
programs. The theorems cover the timing and stuffing mechanisms that the protocols rely on.

### 9.4 Mutation testing

A mutation test injects one deliberate fault and checks that the verification catches it. A
surviving mutant means either a gap in the checks or a fault with no visible effect, which
then needs an argument.

**RTL mutants (`test/core/mutate.sh`), 20.** Each changes one line of `pe_core.v`. The faults
cover: the `sync` flag bit, CRC alignment padding, run-counter saturation, the stuff-due
comparison, the DLY stall comparison, CRC feedback, the `x--` condition, RX FIFO full, the
USB stuff-bit value, NRZI decode polarity, differential output polarity, bit reversal, the
`x!=y`, `due` and `txe` conditions, raw peek vs decoded, the overrun threshold, CRC masking on
`MOV`, CRC preset priority, and ISR shift input. Result: 19 detected. The survivor (USB
stuff-bit value written as `~last`) is proved equivalent by `ones_stuffbit`. Before running the
mutants, the script checks that the unmodified RTL passes with full coverage at the same seed
budget, so a mutant cannot survive just because its feature was not exercised.

**Lean-model mutants, 8.** Run-length saturation, CRC alignment shift, WAIT stall, `x!=y`, NRZI
decode, differential output, the `due` condition, and bit reversal. All 8 are detected by the
Lean vs Python cross-check.

**Firmware mutants (`test/fw_mutate.sh`), 37.** At least one for each of the 23 programs, including faults aimed at the timing checks (I2C low phase, 1-Wire re-anchor)
and at every parity and Manchester decoder path in Modbus RTU and MIL-STD-1553. All 28 are
detected by the protocol tests. Adding mutants for the first four programs found two weak tests.
A UART receiver sampling near the bit edge passed, because the test sent bits at the receiver's
own rate. The test now sends at +-5% bit-rate error, and a sweep showed the original receiver
failed a transmitter 4% fast: edge detection starts the bit timer 3 cycles late. `uart_rx` and
`usb_rx` now subtract this latency. An SPI controller sampling MISO before the SCK rise passed,
because the target model answered within one clock. The model now has a 104 ns output delay.

**Gaps found by mutation testing, all closed:**

| Survivor | Cause | Fix |
|---|---|---|
| run-counter saturation (Lean) | random traces rarely produced 7 equal bits | stuffing-stress programs and a run-length-7 coverage bin |
| `x != y` inverted (RTL) | at the 60-seed mutation budget the branch never executed | uniform branch-condition generation and a baseline coverage gate |
| 1553 RX Manchester check on a 1 bit in the even-parity copy | the error word hit only one of four check paths | error words for all four paths |
| 1553 RX and Modbus RX odd-parity paths | parity-error words had even weight only | added odd-weight error cases |

### 9.5 Protocol tests (`test/`)

25 cocotb tests drive the full top level, including the SPI host port, and check the pins
with reference models (section 8). The same tests pass in all three builds:

| Build | Engines | Result |
|---|---|---|
| behavioural 256-word memory | 4 | 25/25 |
| IHP 256x16 SRAM functional model | 4 | 25/25 |
| 128-word flip-flop store | 2 | 25/25 |

`four_engines` runs UART TX, CAN TX, UART RX and a second UART TX on 4 engines at the same
time and resets one engine during traffic. Two top-level faults (engine select ignored, pin
outputs taken from engine 0 only) were injected by hand, not by a script, and both were
detected.

**Timing checks against protocol limits (48 MHz):** I2C SCL, MDIO MDC, every 1-Wire pulse,
WS2812 high and period, DMX512 break and MAB, LIN break and delimiter, UART edge grid, Modbus
t3.5 silence, 1553 edge grid, and Ethernet link pulses. Values are given per protocol in
section 8.

### 9.6 Incidents in the verification process

- **Leftover mutant.** An interrupted RTL mutation run left a mutant in `src/pe_core.v` (`x != 0` changed to `x != 1`). The next run's baseline check caught it.
  - Checks that ran before the mutant was written were unaffected.
  - One synthesis run used it and was repeated. It had reported 142,102 um2 instead of 142,137 um2.
  - The file was restored from the previously delivered archive and checked by diff.
  - Both mutation scripts now work on private copies of `src/` and `fw/`, and the regression records an MD5 check that the source tree is unchanged.
- **Test-harness errors found by review of suspicious results:**
  - a miscounted expected-word total in the Modbus receive test, which waited for its time limit instead of failing
  - fixed waits that became too short when the clock changed, now replaced by polling
  - a 1553 test that started with an invalid bus state
  - a 1553 edge check that counted the scratch pin
  - strobe leakage between random cases

### Receiver bit-rate tolerance (measured)

Every receiver here starts its bit timer from a sync at the start of a frame, so phase error
accumulates. Sweeping the transmitter bit rate in the testbench gives:

| Receiver | Before | After latency compensation and resync | Specification expects |
|---|---|---|---|
| UART, 10-bit frame | +6/-4% | +-5% | about +-5% |
| CAN receive, 500 kb/s, up to ~130 bits | +0.35/-0.45% | +-0.7% on the test frames, +-0.4% worst case | resynchronisation on every edge |
| USB low speed, 1.5 Mb/s, ~110 bits | +0.4/-0.6% | unchanged | +-1.5% |
| MIL-STD-1553 receive, 20-bit word | -0.2/+0.5% | -0.25/+0.7% | bit rate is +-0.1%, so this covers it |

Two fixes were applied. Each receiver subtracts the 3-clock edge-detection latency (two
synchroniser flip-flops plus the WAIT cycle) from its first half-bit delay, which centres the
window. The CAN and USB data loops also hard-sync on stuff bits: when the stuffing unit reports a
stuff bit is due (`JMP due`), the next bit is the complement of the current line level, so its
edge time is known and the firmware waits for it and re-anchors the timer.

That second fix is data dependent, which is its limitation. Stuff bits occur only after a run of
equal bits, so a frame with no long runs produces no resync events and the tolerance stays at the
free-running value. CAN improved on the test frames because they include 0xFF data; the USB test
packets contain no run of six ones, so USB did not improve at all. Bit stuffing guarantees a
transition at least every 5 bits (CAN) or 7 bits (USB), but not at a time the firmware can predict,
and only a predictable edge can be waited for.

The general fix is a hardware hard-sync: a configuration bit naming a pin whose edges clear the
`since` counter, so the deadline timer re-anchors on every line transition, which is what CAN
resynchronisation and a USB DPLL do. That is roughly ten flip-flops in `pe_core.v` plus matching
changes to the Python and Lean models, the differential test and the mutants. It is not
implemented.

## 10. Synthesis and timing

**Synthesis** (Yosys 0.69, sg13cmos5l typical 1.20 V 25 C, SRAM as a black box):

| Build | Standard-cell area | Flip-flops | With SRAM | Share of die |
|---|---|---|---|---|
| 4 engines, SRAM | 0.142 mm2 | 1311 | 0.254 mm2 | 28% |
| 2 engines, flip-flop store | 0.412 mm2 | 4819 | not applicable | 45% |

**Static timing before layout.** OpenSTA through the PyPI `openroad` package. Slow corner
1.08 V 125 C, 20.83 ns period (48 MHz), ideal clock, no wire parasitics.

| Check | Result |
|---|---|
| Worst setup slack | +11.5 ns |
| Fetch loop (SRAM output -> decode -> next PC -> SRAM address) | 8.4 ns, plus 0.4 ns SRAM setup, 12.8 ns slack |
| Hold | -0.30 ns into the SRAM address pins (0.77 ns macro hold requirement). Must be fixed by the flow's hold repair. |

Input and output delays were set to 0, which is optimistic for the Tiny Tapeout I/O mux. All
protocol and host inputs pass through 2-flop synchronisers. Post-layout timing will be lower
than these numbers, and only the Tiny Tapeout flow can confirm the 48 MHz target.

## 11. Known issues and open items

**Known issue 1: pin combining across engines (not fixed, not tested).** `project.v` ORs each
engine's `pout` and `poe` across engines. Six programs write pin 7 as a scratch bit with its
output disabled: `i2c_ctl`, `mdio`, `onewire`, `ws2812`, `eth_tx` and `mil1553_tx`. If another
engine drove pin 7 as an output at the same time, the scratch writes would corrupt it. No
current program drives pin 7, and `can_rx` only reads it, so present combinations are
unaffected. The fix is to combine `pout & poe` per engine. It is one line plus a test.

**Open items:**

1. Choose the build: 4 engines with SRAM (needs the custom LibreLane power step) or 2 engines with flip-flops (standard flow).
2. Place and route, post-layout timing and precheck in the Tiny Tapeout flow.
3. Protocol gaps: Modbus 1.5-character check, 1553 bus controller and remote terminal logic, CAN resynchronisation and error frames, USB clock recovery and enumeration, SWD writes, PS/2 host-to-device, 10 Mb/s Ethernet.
4. Pin budget: route `ui_in[7:3]` and `uo_out[7:4]` to the engines (needs 4-bit pin indices) to go from 8 to 17 protocol pins.
5. I/O mux speed on this shuttle: not measured, and not given by the competition page.

## 12. Reproducing the results

```
make -C test                                    # 25 tests, 4 engines, behavioural memory
make -C test SRAM_MODEL=yes PDK_ROOT=<ihp-pdk>  # with the IHP SRAM functional model
make -C test IMEM=flop                          # 2-engine flip-flop build
make -C test/core                               # RTL vs Python model, 200 programs
test/core/mutate.sh                             # 20 RTL mutants
test/fw_mutate.sh [test-name-prefix]            # 37 firmware mutants
cd lean && lake build                           # proofs and cross-check executable
python3 gen_traces.py 500 600 | .lake/build/bin/crosscheck
./run_regression.sh                             # everything, output in logs/verify.log
```

Tools: Icarus Verilog 12, cocotb 2.0.1, Lean 4.15.0, Yosys 0.69 (YoWASP), OpenSTA via the
PyPI `openroad` package, IHP-Open-PDK `dev` branch. `run_regression.sh` and `synth/sta.py`
contain paths from the development machine for Lean and the PDK. Edit them or set
`PDK_ROOT` before use.

## References

The reference models in the testbench follow the documents below. Entries marked with a dagger
were checked against the publisher's document during this work, including revision and date. For
the others the entry gives the defining document for the protocol as implemented, without a
verified revision.

| # | Protocol | Document |
|---|---|---|
| 1 | competition | B. Devlin and A. Singhani, "Can you design a chip? Announcing the protocol emulator ASIC competition", Jane Street Blog, 10 September 2026 † |
| 2 | flow | Tiny Tapeout documentation and CMOS5L Verilog template, https://tinytapeout.com † |
| 3 | process | IHP-Open-PDK, SG13CMOS5L process design kit, `dev` branch † |
| 4 | UART | TIA/EIA-232-F, interface between data terminal equipment and data circuit-terminating equipment employing serial binary data interchange |
| 5 | SPI | Motorola, SPI Block Guide, V03.06, document S12SPIV3/D (clock polarity and phase modes) |
| 6 | I2C | NXP Semiconductors, I2C-bus specification and user manual, UM10204, Rev. 7.0, 1 October 2021 † (fast-mode timing) |
| 7 | JTAG | IEEE Std 1149.1-2013, test access port and boundary-scan architecture |
| 8 | SWD | Arm, Debug Interface Architecture Specification ADIv5.0 to ADIv5.2, IHI0031 |
| 9 | MDIO, Ethernet | IEEE Std 802.3, Clause 22 (MII management, MDC/MDIO timing) and Clause 14 (10BASE-T Manchester signalling, link pulses) |
| 10 | PS/2 | A. Chapweske, "The PS/2 Mouse/Keyboard Protocol", 2003; IBM, Personal System/2 Hardware Interface Technical Reference, 1990 |
| 11 | 1-Wire | Maxim Integrated, Application Note 126, 1-Wire Communication Through Software; DS18B20 data sheet |
| 12 | WS2812 | Worldsemi, WS2812B data sheet (1.25 us bit, 0.4 and 0.8 us high times) |
| 13 | DMX512 | ANSI E1.11, USITT DMX512-A, asynchronous serial digital data transmission standard for controlling lighting equipment |
| 14 | LIN | ISO 17987 series, road vehicles, local interconnect network; LIN Consortium, LIN Specification Package Revision 2.2A, 2010 |
| 15 | CAN | ISO 11898-1, road vehicles, controller area network, part 1; Bosch, CAN Specification Version 2.0, 1991 |
| 16 | USB | USB Implementers Forum, Universal Serial Bus Specification Revision 2.0, 27 April 2000 (low-speed signalling, NRZI, stuffing, CRC-5, CRC-16) |
| 17 | Modbus | Modbus Organization, MODBUS over Serial Line Specification and Implementation Guide V1.02, 20 December 2006 † |
| 18 | Modbus | Modbus Organization, MODBUS Application Protocol Specification V1.1b, 28 December 2006 † |
| 19 | MIL-STD-1553 | MIL-STD-1553B, digital time division command/response multiplex data bus, US Department of Defense, with Notice 2, 1986 |
| 20 | CRC values | G. Cook, Catalogue of Parametrised CRC Algorithms, https://reveng.sourceforge.io/crc-catalogue/ |
