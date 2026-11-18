# PE16 instruction set

The executable definitions are `isa/pe16.py` (Python golden model) and `lean/Pe16/Isa.lean`
(Lean model). This page summarises them. Where this page and the code differ, the code wins.

## State per engine

| Register | Width | Notes |
|---|---|---|
| `pc` | 8 | 256-word instruction store (IHP 256x16 SRAM macro) |
| `x`, `y` | 16 | counters; `JMP x--` / `JMP y--` test then decrement |
| `osr` | 16 | output shift register, filled by `PULL` |
| `isr` | 16 | input shift register, drained by `PUSH` |
| `crc` | 16 | CRC register, width and polynomial set by the host |
| `pout`, `poe` | 8 | pin output and output-enable latches |
| `b` | 1 | last bit from `IN` (decoded) or `OUT` (data-level bit placed on the line) |
| `rxl` | 1 | last sampled line level, used for NRZI decode |
| `s` | 1 | last `OUT stuff` / `IN destuff` was a stuff bit |
| `ov` | 1 | sticky timing overrun (a `DLY` deadline was missed) |
| `since` | 16 | cycles since the last `DLY` retire or `SET CTL sync` (saturating) |
| `last`, `run` | 1, 3 | bit-stuffing unit state |
| TX / RX FIFO | 4 x 16 | host side is live while the engine runs |

Every instruction takes one cycle unless it stalls (`WAIT`, `DLY`, `PULL` on empty,
`PUSH` on full, `HALT`).

## Encoding (`[15:13]` = opcode)

| Op | Mnemonic | Fields | Behaviour |
|---|---|---|---|
| 0 | `JMP c, a` | `c = {[9], [12:10]}`, `a[7:0]` | 0 always, 1 x0, 2 x--, 3 y0, 4 y--, 5 b, 6 s, 7 cz (crc == 0), 8 x!=y, 9 due (stuff bit due), 10 txe (TX FIFO empty); 11-15 never taken |
| 1 | `WAIT pol, i` | `pol[12]`, `i[11:9]` | stall until pin i == pol |
| 2 | `DLY k` / `DLY x` | `[12]` use x, `k[11:0]` | deadline wait, see below |
| 3 | `OUT m, i` | `m[12:11]`, `i[10:8]` | raw, crc (also feed CRC), stuff (CRC + bit stuffing), oe |
| 4 | `IN m, i` | `m[12:11]`, `i[10:8]` | raw, crc, destuff, peek (raw level into `b`; NRZI state untouched) |
| 5 | `SET d, ...` | `d[12:10]`, `v[9:0]` | pin i v, oe i v, x, y, ctl, pins (byte), oes (byte) |
| 6 | `MOV d, s` | `d[12:10]`, `s[9:7]`, `~[6]`, `^[5]`, `@[4]` | regs x y osr isr crc pins oe null; applied in order: `~` inverts, `^` left-justifies a CRC-width value and pads the low bits with ones, `@` bit-reverses 16 bits |
| 7 | `PULL` `PUSH` `HALT` `NOP` | `f[12:11]` | FIFO access, stop |

`SET CTL` flags: `crc0`, `crc1` (all ones at CRC width), `stf` (reset stuffing unit),
`isr0`, `ov0`, `sync` (re-anchor the deadline timer to this cycle).

### Deadline timing

`DLY k` retires on the first cycle at which `since + 1 >= k`, then sets `since = 0`.
If `since + 1 > k` on arrival, the deadline was missed and `ov` is set. Consecutive `DLY k`
retires are therefore exactly `k` cycles apart whenever the code between them takes fewer
than `k` cycles, independent of which branch path it took (Lean theorems `dly_retire`,
`dly_period`, `since_incr`).

### Line coding

With `nrzi` set, `OUT raw/crc/stuff` drives the pin as NRZI (data 0 toggles the line, 1 holds it)
and `IN raw/crc/destuff` decodes relative to `rxl`. With `diff` set, those `OUT` forms also drive
pin i+1 with the complement (USB D+/D-). `OUT oe` and `IN peek` are unaffected.

### Bit stuffing

`stuff_n` = run length that forces a stuff bit (0 disables). Identical-bit mode (CAN)
counts equal bits and stuffs the complement. Ones mode (USB) counts ones and stuffs a 0.
`OUT stuff` emits either a stuff bit (does not consume `osr`, sets `s`) or the next data
bit. `IN destuff` discards a stuff bit and sets `s`.

Do not use `stuff_n = 1` in identical-bit mode: the unit stuffs forever
(Lean theorem `stuff1_livelock`, confirmed in the Python model).

## Host port (SPI mode 0, `ui[0]` SCK, `ui[1]` CS_N, `ui[2]` MOSI, `uo[0]` MISO)

Command byte = `{engine[1:0], code[5:0]}`, MSB first. SCK must be at most clk/8.


| Code | Name | Payload |
|---|---|---|
| 1 | IMEM write | address byte, then 16-bit words (auto-increment); ignored while running |
| 2 | CFG write | reg byte, 16-bit value. Engine-local: reg 0 = CRC polynomial; reg 1 = `{diff[11], nrzi[10], stuff_ones[9], stuff_n[8:6], isr_lsb[5], osr_lsb[4], crcw[3:0]}` (crcw = width - 1). Global: engine 0, reg 2 = pin ownership |
| 3 | TX push | 16-bit words |
| 4 | RX pop | reads 16-bit words (reads 0 when empty) |
| 5 | STATUS | reads `{pc[7:0], run, halt, ov, 5'b0}` |
| 6 | COUNTS | reads `{10'b0, rx_count[2:0], tx_count[2:0]}` |
| 7 | CTRL | one byte: bit 0 = run, bit 1 = engine reset |
| 8 | OWNER read | reads the global 16-bit pin ownership register |


### Protocol pin ownership

The eight bidirectional protocol pins (`uio[7:0]`) are shared by all engines.
A global 16-bit register, `PIN_OWNER`, assigns exclusive output control of
each pin to one engine.

For pin `p`, `PIN_OWNER[2*p +: 2]` selects its owner:

| Value | Owner |
|---|---|
| `00` | Engine 0 |
| `01` | Engine 1 |
| `10` | Engine 2 |
| `11` | Engine 3 |

Only the selected engine's `pout[p]` and `poe[p]` drive the physical
output and output-enable signals. Other engines cannot affect that pin's
output, even if their output-enable bits are set.

All engines can independently sample all eight protocol inputs.

#### Configuration

Write ownership using the SPI CFG command with engine ID 0 and
register address 2:

`02 02 HH LL`

Here, `HH LL` is the new 16-bit ownership value, transmitted MSB first.

Read ownership using the OWNER command (code 8):

`08 00 00`

The two dummy bytes clock out the 16-bit register value.

For example, `PIN_OWNER = 0x10C0` assigns:

- Pin 0 to Engine 0 (UART TX)
- Pin 3 to Engine 3 (UART TX)
- Pin 6 to Engine 1 (CAN TX)
- All other pins to Engine 0

#### Reset and ownership rules

- Global reset clears `PIN_OWNER` to zero, assigning all pins to Engine 0.
- Engine-specific resets do not modify global ownership.
- If the owner disables its output, the physical pin becomes high-impedance.
- In a reduced-engine configuration, assigning a pin to a nonexistent engine
  leaves that pin's output disabled.
- Ownership should be configured before starting the affected engines to
  avoid unintended transitions during ownership changes.
- All pin ownership changes take effect together when the CFG write completes.


## Assembler (`isa/pe16.py: assemble`)

`label:` prefixes, `;` comments, `.equ NAME expr`, and `{expr}` substitutions evaluated with
`ns(t)`, `us(t)`, `ms(t)` (round up to whole cycles) and `bit(baud)` (nearest cycle) at
`CLOCK_HZ` (48 MHz) or the `clock_hz` argument. `DLY` immediates outside 1..4095 and `SET`
immediates outside 0..1023 are rejected.
