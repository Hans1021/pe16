
# PE16: Programmable Protocol Engine

**Jane Street Protocol Emulator ASIC Competition — IHP SG13CMOS5L, Tiny Tapeout 8×4**

PE16 is a programmable protocol processor with four independent 16-bit engines. Instead of implementing dedicated hardware for each communication protocol, it executes protocol-specific firmware using an instruction set optimized for precise bit-level timing.

The design includes configurable CRC computation, bit stuffing/destuffing, NRZI and differential signaling, and independently programmable instruction memories. It has completed the Tiny Tapeout ASIC hardening and precheck workflows and has been implemented and tested on a Digilent Basys3 FPGA.

**Submission deadline:** January 18, 2027.

## Architecture

| Feature | Specification |
|---|---|
| Process | IHP SG13CMOS5L (130 nm) |
| ASIC allocation | Tiny Tapeout 8×4 tiles |
| Engines | 4 independent 16-bit processors |
| Instruction memory | 256 × 16 bits per engine, IHP SRAM |
| System clock | 48 MHz |
| Protocol I/O | 8 shared bidirectional pins |
| Host interface | SPI mode 0 |
| Per-engine FIFOs | 4 × 16-bit TX and RX |
| Pin routing | Programmable ownership and validity mask |
| Alternative implementation | 2-engine flip-flop instruction memory |

### Protocol engines

Each engine executes a compact 16-bit ISA supporting conditional branches, GPIO control, shift-register operations, and deadline-based delays (`DLY`). The instruction set also incorporates CRC, bit stuffing/destuffing, NRZI, and complementary differential output.

A shared SPI host interface supports firmware loading, configuration readback, FIFO transfers, status queries, and independent engine control.

All eight protocol pins have programmable ownership. Only the selected engine can drive a pin, while all engines can sample its input. A validity mask keeps unassigned pins high-impedance.

The ASIC uses four IHP 256×16 SRAM macros. Simulation supports behavioral memory and a reduced two-engine flip-flop configuration; the FPGA uses inferred block RAM.

See [docs/isa.md](docs/isa.md) for the complete ISA and host command specification.

## Protocol support

PE16 provides 23 firmware programs covering 16 communication protocols.

| Category | Protocols |
|---|---|
| Serial communication | UART, SPI controller/target, I²C controller/target |
| Debug interfaces | JTAG, SWD |
| Peripheral interfaces | 1-Wire, PS/2, MDIO |
| Industrial communication | CAN, LIN, DMX512, Modbus RTU, MIL-STD-1553 |
| Other signaling | USB low-speed packets, WS2812, 10BASE-T-style Manchester framing |

Capabilities and limitations vary by firmware. Some implementations demonstrate selected transactions or physical signaling rather than complete standards-compliant protocol stacks.

See [docs/protocols.md](docs/protocols.md) for detailed functionality, timing, and limitations.

## Verification

PE16 uses multiple complementary verification methods:

| Method | Coverage |
|---|---|
| Cocotb | 36 top-level tests with independent protocol reference models |
| Differential testing | Randomized RTL comparison against a cycle-accurate Python model |
| Lean 4 | ISA model and eight timing/bit-stuffing theorems |
| Mutation testing | Fault injection into RTL, firmware, and reference models |
| ASIC implementation | Completed Tiny Tapeout GDS hardening and precheck |
| FPGA testing | Real hardware validation using Basys3 and Analog Discovery 3 |

The completed ASIC workflow includes physical implementation with four SRAM macros. Successful hardening and precheck do not constitute fabricated-silicon validation.

### FPGA hardware validation

The four-engine design was implemented on a Digilent Basys3 at 48 MHz and tested using an Analog Discovery 3. The host SPI operated at 100 kHz during hardware experiments.

| Test | Verified behavior | Evidence |
|---|---|---|
| Four-engine execution | Concurrent 6, 12, 24, 48 kHz outputs | [Capture](fpga/results/screenshots/four_engines.png) |
| UART TX/RX | 38,400 baud, multi-byte reception and FIFO ordering | [TX capture](fpga/results/screenshots/uart_tx.png) |
| SPI TX/RX | Mode 0, approximately 100 kHz | [TX capture](fpga/results/screenshots/spi_tx.png) |
| I²C | Master transmission and bidirectional ACK loopback | [TX capture](fpga/results/screenshots/i2c_tx.png) |
| 1-Wire | Reset/write timing, presence detection and negative control | [Timing capture](fpga/results/screenshots/onewire_tx.png) |
| Manchester | Encoded transmission and RX loopback | [TX capture](fpga/results/screenshots/manchester_tx.png) |
| NRZI | Hardware encoding and decoding loopback | SPI readback |
| PWM | 12 kHz at 25% and 75% duty cycles | [25%](fpga/results/screenshots/pwm_25.png) / [75%](fpga/results/screenshots/pwm_75.png) |

Independent engine reset/reprogramming, pin ownership, and configuration readback were also verified.

The [FPGA source files](fpga/basys3/), [test scripts](fpga/tests/), and [implementation reports](fpga/results/) are included in the repository.

See [VERIFICATION.md](docs/verification.md) for measured results and verification details.

## Repository structure

| Path | Contents |
|---|---|
| [src/](src/) | Verilog RTL and Tiny Tapeout top module |
| [isa/](isa/) | Assembler and Python golden model |
| [fw/](fw/) | Protocol firmware |
| [test/](test/) | Cocotb protocol and multi-engine tests |
| [test/core/](test/core/) | Differential ISA verification |
| [lean/](lean/) | Lean 4 model, proofs, and cross-checking |
| [fpga/basys3/](fpga/basys3/) | Basys3 wrapper, constraints, and build scripts |
| [fpga/tests/](fpga/tests/) | FPGA protocol test scripts |
| [fpga/results/](fpga/results/) | WaveForms captures and Vivado reports |
| [synth/](synth/) | ASIC synthesis experiments |
| [docs/](docs/) | Technical report, ISA, and protocol documentation |

## Reproducing the results

### RTL simulation

```bash
make -C test
make -C test IMEM=flop
make -C test SRAM_MODEL=yes PDK_ROOT=<ihp-pdk>
```

### Differential and mutation testing

```bash
make -C test/core
test/core/mutate.sh
test/fw_mutate.sh
```

### Lean verification

```bash
cd lean
lake build
python3 gen_traces.py 500 600 | .lake/build/bin/crosscheck
```

### ASIC implementation

The GitHub Actions workflows perform RTL verification, synthesis, GDS hardening, and Tiny Tapeout precheck. Build outputs and detailed logs are available through the workflow artifacts.

### FPGA implementation

The [Basys3 directory](fpga/basys3/) contains the FPGA-specific build inputs. The [test scripts](fpga/tests/) generate host SPI transactions for programming and exercising PE16 through Digilent WaveForms.

Post-synthesis and post-implementation results are available in [fpga/results/](fpga/results/).

## Documentation

- [Technical report](docs/report.md)
- [LaTeX/PDF report](docs/report/)
- [Verification record](docs/verification.md)
- [Instruction set and host interface](docs/isa.md)
- [Protocol firmware reference](docs/protocols.md)

## Status and limitations

The four-engine RTL, automated verification infrastructure, FPGA implementation, and Tiny Tapeout ASIC hardening flow are complete. Fabricated silicon has not yet been tested.

Remaining limitations include CAN receiver resynchronization, USB clock recovery and enumeration, and full-rate 10BASE-T operation. Protocols requiring specialized physical layers, such as CAN and MIL-STD-1553, also require external transceivers for connection to real buses.
