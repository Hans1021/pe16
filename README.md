# PE16: a programmable protocol engine for Tiny Tapeout (IHP CMOS5L, 6x4)

Entry for the Jane Street protocol emulator ASIC competition (deadline 18 January 2027).
Protocols run as firmware on small engines whose ISA is built for pin timing: a deadline
`DLY` instruction, a host-configurable CRC unit, and a CAN/USB bit-stuffing unit.
The ISA has three executable models kept in agreement by test, and the timing and stuffing
claims are proved in Lean 4.

| Path | Contents |
|---|---|
| `src/` | RTL: `pe_core.v` (engine), `pe_imem.v` (IHP SRAM wrapper), `project.v` (TT top, SPI host port) |
| `isa/pe16.py` | assembler and cycle-accurate golden model |
| `fw/` | firmware for 23 protocol programs, listed in `docs/protocols.md` |
| `test/test.py`, `test/test_protocols.py`, `test/test_cps.py`, `test/test_multi.py` | 25 top-level cocotb tests through the SPI port, with independent protocol reference models |
| `test/fw_mutate.sh` | firmware fault injection against the protocol tests |
| `test/core/` | RTL vs golden model differential test, coverage bins, mutation script |
| `lean/` | Lean 4.15.0 model, proofs, and the Lean vs Python trace cross-check |
| `synth/` | Yosys synthesis logs against the sg13cmos5l typical library |
| `docs/REPORT.md` | technical report: architecture, RTL, every protocol, verification, timing, open items |
| `docs/report/` | the same report in LaTeX with TikZ figures (`pe16_report.tex`, `pe16_report.pdf`) |
| `docs/isa.md` | ISA and host protocol |

## Reproduce

```
make -C test                                   # 25 tests, 4 engines (behavioural SRAM)
make -C test IMEM=flop                         # same, 2-engine flip-flop fallback
make -C test SRAM_MODEL=yes PDK_ROOT=<ihp-pdk> # same, with the IHP 256x16 SRAM model
test/fw_mutate.sh [test-name-prefix]           # firmware mutants vs protocol tests
make -C test/core                              # 200 random programs, RTL == golden model
test/core/mutate.sh                            # RTL mutants vs the differential test
cd lean && lake build                          # proofs + cross-check executable
python3 gen_traces.py 500 600 | .lake/build/bin/crosscheck
```

## Status (see VERIFICATION.md for the evidence)

Done: 4-engine RTL (2 with the flip-flop fallback); UART, SPI (both roles), I2C (both roles), JTAG, SWD read, PS/2 receive, 1-Wire,
WS2812, DMX512, LIN, MDIO, CAN transmit and receive, USB low-speed packet transmit and receive, and
10BASE-T-style Manchester framing at reduced rate, Modbus RTU transmit and receive, and
MIL-STD-1553 word transmit and receive. Limits per protocol are in `docs/protocols.md`.
Four engines run concurrently in simulation (two with the flip-flop fallback). Clock: 48 MHz.
Open: place-and-route and static timing in the TT flow, the LibreLane power step for the SRAM
macros (or the 2-engine flip-flop build), CAN resynchronisation, USB clock recovery, full-rate
10BASE-T.
