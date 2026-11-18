# PE16 Verification Record

PE16 is verified at the ISA, protocol, and system levels using randomized differential tests, Lean 4 proofs, cocotb regressions, mutation testing, ASIC physical implementation, and FPGA hardware measurements. The complete design has four independent protocol engines operating at 48 MHz, each with a private 256×16 instruction memory.

## 1. Three models of the ISA

The instruction set has three executable representations: the [Verilog RTL](../src/pe_core.v), [Python golden model](../isa/pe16.py), and [Lean 4 model](../lean/Pe16/Isa.lean).

| Check | Method | Result |
|---|---|---|
| RTL vs. Python | 200 random programs × 600 cycles; 18 state fields compared every cycle | 0 mismatches; 80/80 coverage bins |
| Lean vs. Python | 500 random traces × 600 cycles replayed in Lean | 0 mismatches across 300,000 cycles |
| Lean proofs | Properties of instruction timing and bit stuffing | 8 theorems, no `sorry` |

The RTL differential test exercises random pin inputs, FIFO traffic, register operations, conditional branches, and CRC, stuffing, NRZI, and differential configurations. Directed stuffing-stress programs cover long equal-bit sequences, while uniform branch-condition generation ensures coverage of less frequently taken conditions.

The RTL and Lean models are connected by differential execution against the Python model; the RTL itself has not been formally proved equivalent to Lean.

## 2. Mutation testing

Mutation testing evaluates how effectively the verification infrastructure detects deliberately introduced defects.

| Check | Faults injected | Detected |
|---|---:|---:|
| RTL vs. Python (`test/core/mutate.sh`) | 20 RTL mutants | 19 |
| Lean vs. Python | 8 Lean mutants | 8 |
| Protocol tests (`test/fw_mutate.sh`) | 37 firmware mutants | 28 |
| Multi-engine test | Broken engine selection and pin combining | 2/2 |

The one surviving RTL mutant changes the USB stuffing-bit calculation to an equivalent expression, established by the Lean theorem `ones_stuffbit`.

The firmware mutations cover instruction timing, protocol framing, and parity/Manchester decoder paths in Modbus RTU and MIL-STD-1553. The 28 detected mutations represent the measured detection coverage of the original protocol suite.

Verification development exposed several coverage gaps: long identical-bit runs were underrepresented, the `x != y` branch was not reached within the initial mutation budget, and fixed test waits sometimes expired before firmware completed. These were addressed with stress programs, uniformly generated branch conditions, a baseline coverage gate, and status-based polling.

### Verification incidents

An interrupted mutation run once left an injected RTL mutation in `src/pe_core.v`. The baseline differential test caught it at the start of the next run. The source was restored and the affected synthesis result repeated. Both mutation scripts now use private source copies, and the regression checks that the original files remain unchanged.

Test-harness review also corrected an expected-word count in Modbus receive testing and two MIL-STD-1553 checks involving initial bus state and an unused scratch pin.

The original regression records are in [logs/verify.log](../logs/verify.log).

## 3. Protocol tests

The expanded cocotb suite contains **36 top-level tests**, covering the 23 firmware programs, concurrent execution, pin ownership, configuration readback, reset, SPI robustness, and FIFO edge cases.

The reference models observe protocol pins and host SPI transactions rather than internal engine state. They check the resulting waveforms, framing, CRCs, and timing against independent protocol implementations.

| Memory build | Engines | Regression results |
|---|---:|---|
| Behavioral 256-word RAM | 4 | Original 25/25 passed |
| IHP 256×16 SRAM functional model | 4 | Original 25/25 and expanded 36-test CI passed |
| 128-word flip-flop fallback | 2 | Original 25/25 passed |
| Post-layout gate-level netlist | 4 | 36/36 passed |

The RTL workflow is defined in [.github/workflows/test.yaml](../.github/workflows/test.yaml), and the tests are in [test/](../test/).

### Protocol coverage

| Protocol | Reference-model checks |
|---|---|
| UART | Transmit/receive bytes, framing, bit timing |
| SPI | Controller/target transfers in both directions |
| I²C | Controller/target reads and writes, ACK/NACK, clock stretching |
| JTAG | TAP state transitions and IDCODE read |
| SWD | Line reset, DPIDR read, ACK and parity |
| PS/2 | Device-to-host scan-code reception |
| 1-Wire | Reset, presence, ROM read, and slot timing |
| WS2812 | Encoded LED data and pulse widths |
| DMX512 | Break, mark-after-break, and serial frames |
| LIN | Break, synchronization, identifier, and checksum |
| MDIO | Clause 22 reads/writes and turnaround |
| CAN | Standard/extended frames, CRC, stuffing, reception, and ACK |
| USB low-speed | Packet TX/RX, CRC-5/16, NRZI, stuffing, differential signaling |
| Manchester | Frame encoding and link pulses |
| Modbus RTU | TX/RX, parity, CRC, and frame timing |
| MIL-STD-1553 | Manchester II words, parity, malformed data, and timing |

CRC references use published check values, including CRC-15/CAN `0x059E`, CRC-16/USB `0xB4C8`, CRC-5/USB `0x19`, and CRC-16/Modbus `0x4B37`. The Modbus tests also check the reference request `11 03 00 6B 00 03` with CRC bytes `76 87`. USB receive tests verify configured CRC residues and rejection of corrupted packets.

### Measured protocol timing

At a 48 MHz system clock:

| Protocol | Simulation measurement |
|---|---|
| I²C fast mode | SCL LOW ≥1.5 µs, HIGH ≥1.1 µs, ≤384 kHz |
| MDIO | HIGH/LOW 208 ns, period 417 ns |
| 1-Wire | Reset 480.1 µs; write pulses within standard-speed windows |
| WS2812 | HIGH 417/813 ns, period 1.25 µs |
| DMX512 | Break 90 µs, MAB 12 µs |
| LIN | Break 13 bits, delimiter one bit |
| Modbus RTU | Inter-frame silence 1759 µs |
| MIL-STD-1553 | Edges aligned to the 500 ns half-bit grid |
| Manchester NLP | Width 104 ns, interval 16.000 ms |

The timing checks found two firmware defects during development: an I²C SCL LOW interval of 1.0 µs and a 1-Wire reset pulse of 479.9 µs. Both were corrected.

### Receiver bit-rate tolerance

Receiver tolerance was measured by sweeping the transmitter clock relative to the nominal firmware timing.

| Receiver | Measured tolerance |
|---|---|
| UART | Approximately ±5% |
| CAN | ±0.7% on tested frames; ±0.4% without stuff bits |
| USB low-speed | +0.4% / −0.6% |
| MIL-STD-1553 | −0.25% / +0.7% |

All receivers compensate for synchronized-input edge-detection latency. CAN and USB additionally synchronize on selected bit-stuffing events. Their rate tolerance depends on the frame pattern and available transitions.

## 4. Lean theorems

The proofs are in [lean/Pe16/](../lean/Pe16/).

| Theorem | Statement |
|---|---|
| `dly_retire` | `DLY k`, reached with `since = c < k`, retires after exactly `k − c` cycles, resetting `since` without changing `ov` |
| `dly_period` | Consecutive deadline retirements are separated by exactly `k` cycles |
| `since_incr` | Instructions other than `DLY` and `SET CTL sync` advance `since` by one |
| `no_long_runs` | Identical-bit stuffing with threshold 1–7 prevents runs longer than the threshold |
| `roundtrip` | Stuffing and destuffing preserve the original sequence of data bits |
| `stuff1_livelock` | Identical-bit stuffing with threshold 1 causes continuous stuffing |
| `stuff_progress` | Thresholds of at least 2 prevent consecutive stuffed bits |
| `ones_stuffbit` | In USB mode the inserted stuff bit equals the complement of the preceding bit |

The proofs compile with Lean 4.15.0 using standard axioms and no `sorry`.

## 5. ASIC physical implementation

The ASIC targets **IHP SG13CMOS5L**, using four `256×16` SRAM macros in a Tiny Tapeout **8×4** allocation. The GitHub Actions flow completed GDS hardening, post-layout timing analysis, LVS, Tiny Tapeout precheck, and gate-level verification.

### Final area and timing

| Measurement | Result |
|---|---:|
| Die dimensions | 1724.16 × 710.64 µm |
| Die area | 1.2253 mm² |
| Standard-cell area | 0.2212 mm² |
| SRAM macro area | 0.1125 mm² |
| Combined area | 0.3337 mm² |
| Core utilization | 27.62% |
| Standard-cell instances | 14,955 |
| Sequential cells | 1,335 |
| SRAM macros | 4 |
| Worst setup slack | +6.3437 ns |
| Worst hold slack | +0.1025 ns |
| Setup/hold violations | 0 |

Timing was evaluated at fast (1.32 V, −40°C), typical (1.20 V, 25°C), and slow (1.08 V, 125°C) corners. At the 48 MHz target clock, setup and hold timing passed across all three.

| Corner | Setup slack | Hold slack |
|---|---:|---:|
| Fast | +12.6413 ns | +0.1025 ns |
| Typical | +10.9540 ns | +0.2800 ns |
| Slow | +6.3437 ns | +0.5968 ns |

The earlier pre-layout synthesis estimate was 0.142 mm² of standard cells plus 0.112 mm² of SRAM, or approximately 0.254 mm² combined. The final area includes placement, buffering, clock-tree, and timing-repair overhead.

### Physical verification

| Check | Result |
|---|---|
| GDS hardening | Passed |
| Routed DRC | 0 errors |
| LVS | Matched |
| Tiny Tapeout precheck | All 9 checks passed |
| Post-layout gate-level regression | 36/36 passed |
| Layout viewer | Generated |

The final timing report includes 37 maximum-slew and 11 maximum-capacitance violations. An earlier power-grid checker also reported violations with its failure condition disabled. These electrical and power-grid diagnostics remain available in the implementation reports.

The flow configuration is in [.github/workflows/gds.yaml](../.github/workflows/gds.yaml). Full physical-design results, gate-level test output, and layout files are available through GitHub Actions artifacts.

## 6. FPGA hardware validation

PE16 was implemented on a Digilent Basys3 with four independent engines and inferred block RAM instruction memories. A Digilent Analog Discovery 3 provided host SPI transactions, digital input stimulus, and WaveForms measurements.

The FPGA runs at 48 MHz. Host SPI was operated at 100 kHz for the hardware experiments.

### Vivado implementation

| Measurement | Post-synthesis | Post-implementation |
|---|---:|---:|
| Slice LUTs | 2,635 | 2,597 |
| Slice registers | 1,362 | 1,362 |
| RAMB18E1 | 4 | 4 |
| DSPs | 0 | 0 |
| Worst setup slack | +9.360 ns | +7.342 ns |
| Worst hold slack | +0.091 ns | +0.063 ns |

Post-implementation utilization is 12.49% of LUTs, 3.27% of registers, and 4.00% of RAMB18E1 resources. Total negative setup and hold slack are both zero.

The [timing](../fpga/results/timing_impl.rpt) and [utilization](../fpga/results/utilization_impl.rpt) reports contain the complete Vivado results. The FPGA wrapper and constraints are in [fpga/basys3/](../fpga/basys3/).

### Protocol hardware tests

| Test | Hardware result | Evidence |
|---|---|---|
| Four-engine execution | Concurrent 6, 12, 24, 48 kHz square waves | [Waveform](../fpga/results/screenshots/four_engines.png) |
| UART | 38,400-baud TX/RX; 26.071 µs TX bit period | [Waveform](../fpga/results/screenshots/uart_tx.png) |
| SPI | Mode-0 TX/RX near 100 kHz; `0x69` transmitted, `0x96` received | [Waveform](../fpga/results/screenshots/spi_tx.png) |
| I²C | Master transmission and inter-engine ACK response | [Waveform](../fpga/results/screenshots/i2c_tx.png) |
| 1-Wire | Reset, write-slot timing, and presence detection | [Waveform](../fpga/results/screenshots/onewire_tx.png) |
| Manchester | Encoded transmission and receive loopback | [Waveform](../fpga/results/screenshots/manchester_tx.png) |
| NRZI | Encode/decode loopback of `0xA6` | SPI readback |
| PWM | 12 kHz, 25% and 75% duty cycles | [25%](../fpga/results/screenshots/pwm_25.png), [75%](../fpga/results/screenshots/pwm_75.png) |

**Multi-engine execution.** Four engines independently produced output frequencies of approximately 6, 12, 24, and 48 kHz on distinct assigned pins. Each executed a separately programmed instruction memory.

**UART.** The transmitted `0x55` pattern decoded correctly at 38,400 baud, with a measured bit period of 26.071 µs. External RX tests recovered `0x55`, `0xAA`, `0x00`, and `0xFF` patterns through the RX FIFO in the expected order.

**SPI.** Mode-0 transmission generated repeated `0x69` bytes with the expected SCK, MOSI, and CS_N timing. External receive testing produced FIFO data `0x0096` after the AD3 transmitted `0x96`.

**I²C.** Master firmware generated START, address `0x50`, NACK, and STOP on an open-drain bus. In a second test, another engine responded with ACK; the master sampled the LOW SDA level and returned `0x0000` through its RX FIFO.

**1-Wire.** The reset pulse measured approximately 481 µs, with write-1 and write-0 LOW intervals of approximately 6.3 µs and 60 µs. A second engine emulated a slave presence pulse. The master distinguished the active responder (`0x0000`) from the no-presence condition (`0x0001`).

**Manchester.** Transmission measured a 9.885 µs bit period and 4.970 µs half-bit interval. Receive loopback successfully reconstructed test frames, including `0x96`. Sustained streaming could lose alignment following RX FIFO backpressure, so the test establishes frame-level encoding and decoding rather than uninterrupted streaming recovery.

**NRZI.** One engine transmitted `0xA6` using the hardware NRZI configuration, while a second decoded the signal and returned `0xA600` through its RX FIFO.

**PWM.** Duty-cycle measurements were:

| Measurement | 25% setting | 75% setting |
|---|---:|---:|
| Frequency | 11.999 kHz | 12.000 kHz |
| Period | 83.340 µs | 83.330 µs |
| Duty cycle | 24.982% | 74.979% |
| HIGH duration | 20.820 µs | 62.480 µs |
| LOW duration | 62.520 µs | 20.850 µs |

Host SPI programming, FIFO status and readback, independent engine reset, firmware replacement, pin ownership, and validity-mask configuration were also exercised in hardware.

The FPGA test scripts are in [fpga/tests/](../fpga/tests/), and the captured waveforms are in [fpga/results/screenshots/](../fpga/results/screenshots/).

## 7. Reproduction

RTL simulation:

```bash
make -C test
make -C test IMEM=flop
make -C test SRAM_MODEL=yes PDK_ROOT=<ihp-pdk>
```

Differential and mutation testing:

```bash
make -C test/core
test/core/mutate.sh
test/fw_mutate.sh
```

Lean proofs and trace cross-check:

```bash
cd lean
lake build
python3 gen_traces.py 500 600 | .lake/build/bin/crosscheck
```

The ASIC workflow runs through GitHub Actions. FPGA build inputs, test scripts, and implementation reports are provided under [fpga/](../fpga/).

The complete architecture and protocol implementation are described in the [technical report](report.md).