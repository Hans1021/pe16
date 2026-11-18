# PE16 Instruction Set

The executable definitions are [isa/pe16.py](../isa/pe16.py) (Python golden model) and [lean/Pe16/Isa.lean](../lean/Pe16/Isa.lean) (Lean model). This page summarizes the instruction set and host interface; the RTL and executable models are authoritative.

## State per engine

| Register | Width | Notes |
|---|---|---|
| `pc` | 8 | 256-word instruction store (IHP 256×16 SRAM macro) |
| `x`, `y` | 16 | Counters; `JMP x--` / `JMP y--` test then decrement |
| `osr` | 16 | Output shift register, filled by `PULL` |
| `isr` | 16 | Input shift register, drained by `PUSH` |
| `crc` | 16 | CRC register; host-configurable width and polynomial |
| `pout`, `poe` | 8 | Pin output and output-enable latches |
| `b` | 1 | Last decoded input or output data bit |
| `rxl` | 1 | Last sampled line level, used for NRZI decoding |
| `s` | 1 | Last stuffing/destuffing operation involved a stuff bit |
| `ov` | 1 | Sticky timing-overrun flag |
| `since` | 16 | Cycles since last `DLY` retirement or `SET CTL sync` (saturating) |
| `last`, `run` | 1, 3 | Bit-stuffing state |
| TX/RX FIFO | 4 × 16 each | Host access remains available while the engine runs |

Every instruction normally takes one cycle. `WAIT`, `DLY`, `PULL` on empty, and `PUSH` on full can stall; `HALT` stops execution.

## Encoding (`[15:13]` = opcode)

| Op | Mnemonic | Fields | Behavior |
|---|---|---|---|
| 0 | `JMP c, a` | `c = {[9], [12:10]}`, `a[7:0]` | 0 always, 1 x0, 2 x--, 3 y0, 4 y--, 5 b, 6 s, 7 cz (CRC zero), 8 x!=y, 9 due (stuff bit due), 10 txe (TX FIFO empty); 11–15 never taken |
| 1 | `WAIT pol, i` | `pol[12]`, `i[11:9]` | Stall until pin `i` equals `pol` |
| 2 | `DLY k` / `DLY x` | `[12]` selects X, `k[11:0]` | Deadline wait |
| 3 | `OUT m, i` | `m[12:11]`, `i[10:8]` | Raw, CRC, stuff, output enable |
| 4 | `IN m, i` | `m[12:11]`, `i[10:8]` | Raw, CRC, destuff, peek |
| 5 | `SET d, ...` | `d[12:10]`, `v[9:0]` | Pin, OE, X, Y, control, all pins, all OEs |
| 6 | `MOV d, s` | `d[12:10]`, `s[9:7]`, `~[6]`, `^[5]`, `@[4]` | Transfer among X, Y, OSR, ISR, CRC, pins, OE, null; optional invert, CRC alignment, bit reversal |
| 7 | `PULL`, `PUSH`, `HALT`, `NOP` | `f[12:11]` | FIFO access or control |

`SET CTL` flags are `crc0` (clear CRC), `crc1` (all ones at configured CRC width), `stf` (reset stuffing state), `isr0` (clear ISR), `ov0` (clear overrun), and `sync` (reset deadline timer). Multiple flags can be combined.

### Deadline timing

`DLY k` retires on the first cycle for which `since + 1 >= k`, then resets `since` to zero. If `since + 1 > k`, the deadline was missed and `ov` is set.

Consecutive `DLY k` instructions therefore retire exactly `k` cycles apart when the intervening instructions complete before the deadline, regardless of branch path. This behavior is established by Lean theorems `dly_retire`, `dly_period`, and `since_incr`.

`DLY x` uses X as the delay value, treating zero as one cycle.

### Line coding

With `nrzi` enabled, `OUT raw/crc/stuff` drives NRZI signaling: data 0 toggles the line, while data 1 preserves its level. The corresponding `IN` modes decode bits relative to the previously sampled line level (`rxl`).

With `diff` enabled, those `OUT` forms also drive the complementary value on pin `(i+1) mod 8`, supporting differential protocols such as USB.

`OUT oe` and `IN peek` are unaffected by line coding. `IN peek` samples the raw pin into `b` without modifying NRZI state.

### CRC and bit stuffing

The CRC unit supports programmable polynomial and width (1–16 bits). `OUT crc/stuff` and `IN crc/destuff` update the CRC using data bits; inserted stuff bits are excluded.

`stuff_n` specifies the run length that triggers stuffing (0 disables it):

- **Identical-bit mode (CAN):** After `stuff_n` consecutive equal bits, insert the complement.
- **Ones mode (USB):** After `stuff_n` consecutive ones, insert zero.

`OUT stuff` emits a required stuff bit without consuming OSR data and sets `s`. `IN destuff` discards the corresponding received bit and sets `s`.

Do not use `stuff_n = 1` in identical-bit mode: the unit stuffs continuously (Lean theorem `stuff1_livelock`).

### MOV modifiers

`MOV` can operate on X, Y, OSR, ISR, CRC, pin values, output enables, and the null register. Three optional source modifiers apply in order:

| Modifier | Operation |
|---|---|
| `~` | Invert the 16-bit value |
| `^` | Left-align the configured CRC width and fill remaining low bits with ones |
| `@` | Reverse the order of all 16 bits |

The modifiers are useful for CRC output formatting and protocol-dependent bit ordering.

## Host SPI interface

The host uses SPI mode 0, MSB-first. SCK must be at most `clk/8`, corresponding to 6 MHz at a 48 MHz system clock.

| Signal | Tiny Tapeout port |
|---|---|
| SCK | `ui_in[0]` |
| CS_N | `ui_in[1]` |
| MOSI | `ui_in[2]` |
| MISO | `uo_out[0]` |

The command byte is `{engine[1:0], code[5:0]}`. The upper two bits select one of four engines, while the lower six identify the operation. Data words are transmitted MSB-first.

| Code | Name | Payload / response |
|---|---|---|
| 1 | IMEM write | Address byte, then 16-bit instruction words (auto-increment); ignored while running |
| 2 | CFG write | Register address, then a 16-bit value |
| 3 | TX push | 16-bit words |
| 4 | RX pop | Reads 16-bit words (zero when empty) |
| 5 | STATUS | Reads `{pc[7:0], run, halt, ov, 5'b0}` |
| 6 | COUNTS | Reads `{10'b0, rx_count[2:0], tx_count[2:0]}` |
| 7 | CTRL | One byte: bit 0 = run, bit 1 = engine reset |
| 8 | OWNER read | Reads global 16-bit pin ownership register |
| 9 | CFG read | Register address, then 16-bit register readback |

### Configuration registers

CFG write (code 2) and CFG read (code 9) use the following register addresses:

| Address | Scope | Contents |
|---|---|---|
| 0 | Per-engine | CRC polynomial |
| 1 | Per-engine | CRC width, shift directions, stuffing, NRZI and differential mode |
| 2 | Global (Engine 0) | `PIN_OWNER`, 16-bit pin ownership |
| 3 | Global (Engine 0) | `PIN_VALID`, eight-bit pin validity mask |

Register 1 is laid out as:

`{diff[11], nrzi[10], stuff_ones[9], stuff_n[8:6], isr_lsb[5], osr_lsb[4], crcw[3:0]}`

`crcw` specifies the CRC width minus one. The default value after global reset is `0x03F`.

Configuration can be read through command 9, including the two global pin-routing registers when Engine 0 is selected. Invalid register addresses return zero.

### Engine control

CTRL command 7 sets the engine RUN state and optionally generates a one-cycle engine reset.

For example, to stop/reset Engine 2, load its firmware, and restart it:

```text
87 02       Stop and reset Engine 2
81 00 ...   Write instructions starting at address 0
87 01       Start Engine 2
85 00 00    Read Engine 2 STATUS
86 00 00    Read FIFO counts
84 00 00    Read one RX FIFO word
```

An engine reset clears its execution state without modifying global pin ownership or validity. The instruction memory can be programmed while the engine is stopped.

Both FIFOs have four entries. `PULL` stalls when TX is empty, and `PUSH` stalls when RX is full. The host can push and pop during execution; COUNTS reports occupancy, allowing a zero-valued received word to be distinguished from an empty FIFO.

## Protocol pin ownership

The eight bidirectional protocol pins (`uio[7:0]`) are shared among all engines. Each pin has a programmable owner, and a separate validity mask controls whether the assignment is active.

Only the selected engine can drive a physical output. All engines can independently sample all eight protocol inputs.

### PIN_OWNER

`PIN_OWNER` is a 16-bit register containing two owner bits per pin:

`PIN_OWNER[2*p +: 2] = owner of pin p`

| Value | Owner |
|---|---|
| `00` | Engine 0 |
| `01` | Engine 1 |
| `10` | Engine 2 |
| `11` | Engine 3 |

For example, `PIN_OWNER = 0x00E4` assigns pins 0–3 to Engines 0–3 respectively, and pins 4–7 to Engine 0.

The ownership assignment selects the corresponding engine's `pout[p]` and `poe[p]` signals. Other engines cannot modify that pin's physical output even if their own output-enable bits are set.

### PIN_VALID

`PIN_VALID` is an eight-bit mask. For each pin `p`:

- `PIN_VALID[p] = 0`: The output is unassigned and high-impedance.
- `PIN_VALID[p] = 1`: The selected engine controls the output and its output enable.

An assigned engine can still release its pin by clearing `poe[p]`. In the two-engine fallback, ownership values referring to nonexistent engines leave the outputs disabled.

### Host configuration

Global routing registers are written through Engine 0 with CFG command 2.

```text
02 02 00 E4    Assign pins 0–3 to Engines 0–3
02 03 00 0F    Enable routing on pins 0–3
```

Read ownership using command 8:

```text
08 00 00       Read PIN_OWNER (0x00E4)
```

Alternatively, use CFG read command 9:

```text
09 02 00 00    Read PIN_OWNER (0x00E4)
09 03 00 00    Read PIN_VALID (0x000F)
```

The last two bytes clock out the 16-bit response. All routing changes take effect after the corresponding CFG write completes.

### Reset behavior

Global reset clears `PIN_OWNER` and `PIN_VALID`, leaving all protocol outputs high-impedance. It also resets and stops the engines.

Engine-specific resets leave global pin ownership and validity unchanged. Pin assignments can therefore remain in place while individual engines are reprogrammed or restarted.

## Assembler

The assembler in [isa/pe16.py](../isa/pe16.py) supports `label:` prefixes, `;` comments, `.equ NAME expr`, and `{expr}` substitutions.

Timing expressions can use `ns(t)`, `us(t)`, and `ms(t)`, which round up to whole cycles, or `bit(baud)`, which rounds to the nearest cycle. Expressions use the default 48 MHz design clock or the supplied `clock_hz` argument.

`DLY` immediates outside 1–4095 and `SET` immediates outside 0–1023 are rejected.

See [protocols.md](protocols.md) for firmware examples and verified protocol behavior.