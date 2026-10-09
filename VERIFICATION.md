# Verification record

Numbers come from `logs/verify.log` (produced by `run_regression.sh`) and
`logs/sta_slow_48MHz.txt`. Tools: Icarus Verilog 12, cocotb 2.0.1, Lean 4.15.0, Yosys 0.69
(YoWASP), OpenROAD/OpenSTA (PyPI `openroad` 0.0.1), IHP-Open-PDK `dev` branch. Everything is
simulation, proof, or pre-layout analysis. Nothing has been placed, routed or fabricated.

## 1. Three models of the ISA

| Link | Method | Result |
|---|---|---|
| RTL `pe_core.v` = Python model | 200 random programs x 600 cycles; 18 state fields compared every cycle; random pins, FIFO traffic and config (CRC, stuffing, NRZI, differential); stuffing-stress programs; branch conditions drawn uniformly | 0 mismatches, 80/80 coverage bins |
| Lean model = Python model | 500 random traces x 600 cycles replayed in the compiled Lean model | 0 mismatches in 300,000 cycles |
| Lean theorems | proved about the Lean model | 8 theorems, no `sorry`, standard axioms only |

The RTL is linked to the Lean model by simulation, not by proof.

## 2. Do the checks detect errors?

| Check | Faults injected | Detected |
|---|---|---|
| RTL vs Python model (`test/core/mutate.sh`, after a baseline full-coverage check) | 20 RTL mutants | 19; the survivor is proved equivalent (`ones_stuffbit`) |
| Lean vs Python cross-check | 8 Lean-model mutants | 8 |
| Protocol tests (`test/fw_mutate.sh`) | 37 firmware mutants, including timing-check mutants and every parity/Manchester decoder path in Modbus RTU and MIL-STD-1553 | 28 |
| Multi-engine test | engine select broken; pin combining from engine 0 only | 2 |

Gaps in the checks that were found and closed: rare long equal-bit runs (stress programs added);
the `x != y` branch never executed at the mutation budget (uniform branch generation, baseline
coverage gate); fixed waits in tests that became too short at a new clock (polling instead).

## 3. Protocol tests

25 tests (24 protocol tests plus one 4-engine concurrency test). Results:

| Build | Engines | Result |
|---|---|---|
| behavioural 256-word memory | 4 | 25/25 |
| IHP 256x16 SRAM functional model | 4 | 25/25 |
| 128-word flip-flop store (`-DPE_FLOP_IMEM`) | 2 | 25/25 |

Reference models see only pins and are written from the protocol rules. CRC references match
catalogue check values (reveng catalogue of parametrised CRC algorithms) (CRC-15/CAN 0x059E, CRC-16/USB 0xB4C8, CRC-5/USB 0x19, CRC-16/MODBUS 0x4B37)
and the widely republished Modbus example frame (`11 03 00 6B 00 03` -> `76 87`); the USB receive
tests confirm the residue constants 0x800D and 0x0C.

Timing measured against the protocol limits (48 MHz): I2C SCL low >= 1.5 us, high >= 1.1 us,
<= 384 kHz; Modbus RTU silence between frames 1759 us (t3.5 = 1750 us); MIL-STD-1553 line
edges all on the 500 ns half-bit grid; MDC high/low 208 ns, period 417 ns; every host-driven 1-Wire low pulse inside the
standard-speed windows (reset 480.1 us); WS2812 high 417/813 ns, period 1.25 us; DMX512 break
90 us, MAB 12 us; LIN break 13 bits, delimiter 1 bit; Manchester NLP 104 ns wide, 16.000 ms apart.
The timing checks found two firmware defects (I2C SCL low 1.0 us; 1-Wire reset 479.9 us), both fixed.

### Incidents in the verification process

- An interrupted RTL mutation run left an injected mutant (`x != 0` changed to `x != 1` in the
  `x--` branch condition) in `src/pe_core.v`. It was caught by the baseline check at the start of
  the next mutation run, which failed on the unmodified-design differential test. Protocol suites,
  firmware mutation and the differential test had run before the mutant was written; one
  synthesis run used it and was repeated. The file was restored from the previously delivered
  archive and checked by diff. Both mutation scripts now work on private copies of `src/` and
  `fw/`, and the regression records an MD5 check that the source tree is unchanged.
- Test-harness errors found by review of suspicious results: a miscounted expected-word total in
  the Modbus receive test (the test waited for its time limit instead of failing), a 1553 test
  that started with an invalid bus state, and a 1553 edge check that counted the unused scratch pin.

## 4. Lean theorems (`lean/Pe16/`)

| Theorem | Statement |
|---|---|
| `dly_retire` | at `DLY k` with `since = c < k`, the engine retires after exactly `k - c` cycles for any inputs, leaving `since = 0` and `ov` unchanged |
| `dly_period` | with `since = m < k` on arrival, the retire is `k` cycles after the previous one |
| `since_incr` | every instruction except `DLY` and `SET CTL sync` adds exactly one to `since` |
| `no_long_runs` | identical-bit mode, `1 <= stuff_n <= 7`, any sequence of `OUT stuff`: no run of equal line bits longer than `stuff_n` |
| `roundtrip` | the bits `IN destuff` accepts from the transmitted line equal the data bits `OUT stuff` consumed, in order |
| `stuff1_livelock` | identical-bit mode with `stuff_n = 1` stuffs forever (also seen in the Python model) |
| `stuff_progress` | for `stuff_n >= 2` a stuff bit is never followed by another |
| `ones_stuffbit` | in USB mode the stuff bit equals `~last` (equivalence of the surviving RTL mutant) |

## 5. Synthesis and pre-layout timing (4 engines, SRAM build)

| Build | Std-cell area | Flip-flops | With SRAM | Share of 6x4 die (0.916 mm2) |
|---|---|---|---|---|
| 4 engines, SRAM macros | 0.142 mm2 | 1311 | 0.254 mm2 | 28% |
| 2 engines, flip-flop store | 0.412 mm2 | 4819 | - | 45% |

Static timing, slow corner (1.08 V, 125 C), 20.83 ns (48 MHz), ideal clock, no wire parasitics:
setup slack +11.5 ns worst; the fetch loop (SRAM output -> decode -> next PC -> SRAM address)
is 8.4 ns plus 0.4 ns SRAM setup. Hold slack -0.30 ns into the SRAM address pins (0.77 ns macro
hold requirement), which the flow's hold repair must fix. Input and output delays are set to 0,
which is optimistic for the Tiny Tapeout I/O mux; all protocol and SPI inputs pass through
two-flop synchronisers.

## 5b. Receiver bit-rate tolerance (measured by sweeping the transmitter rate)

| Receiver | Measured | Specification expects |
|---|---|---|
| UART | +-5% | about +-5% |
| CAN receive | +-0.7% on the test frames, +-0.4% without stuff bits | resynchronisation each bit |
| USB low speed receive | +0.4/-0.6% | +-1.5% |
| MIL-STD-1553 receive | -0.25/+0.7% | bit rate +-0.1%, so covered |

All receivers now compensate the 3-clock edge-detection latency. The CAN and USB data loops also
hard-sync on stuff bits, which helps only frames that contain runs of equal bits. A hardware
hard-sync on pin edges would fix this generally and is not implemented. Default tests run CAN at
+-0.3% and USB at -0.3% to guard these margins.

## 6. Open items

Place-and-route and post-layout timing in the Tiny Tapeout flow; Modbus t1.5 inter-character check;
MIL-STD-1553 bus controller / remote terminal logic; the LibreLane power step for
the SRAM macros (or choosing the 2-engine flip-flop build); CAN and USB receiver resynchronisation (measured tolerance +-0.4% and +0.4/-0.6%); USB clock
recovery; full-rate 10BASE-T.

Protocol specifications used for the reference models are listed in `docs/REPORT.md`.
