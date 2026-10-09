## How it works

PE16 is a programmable bit-level protocol engine. A host loads firmware and configuration
over SPI (`ui[0]` SCK, `ui[1]` CS_N, `ui[2]` MOSI, `uo[0]` MISO), then streams data through
4-deep TX/RX FIFOs. The engine drives and samples `uio[7:0]`. Its ISA has a deadline delay
instruction for jitter-free bit timing, a configurable CRC unit, and a CAN/USB bit-stuffing
unit. See `docs/isa.md`.

## How to test

Load `fw/uart_tx.pe` (assembled with `isa/pe16.py`), set config reg 1 to `0x001F`, run the
engine, push bytes, and watch UART output on `uio[0]` at clk/16 baud.

## External hardware

A microcontroller acting as SPI host (the TT demo board RP2040 works). A CAN transceiver for CAN tests.
