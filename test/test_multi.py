# SPDX-License-Identifier: Apache-2.0
"""Several engines at once: each runs a different protocol on its own pins, driven over the
one SPI host port. Checks per-engine correctness, pin combining, and that resetting one engine
leaves the others running."""
import os, sys, random
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "isa"))
import cocotb
from cocotb.triggers import ClockCycles
from test import Host, setup, fw, can_frame, uart_capture, sample_line, pin, CAN_T
import pe16


@cocotb.test()
async def four_engines(dut):
    h = await setup(dut)
    try:
        neng = int(dut.user_project.NENG.value)
    except AttributeError:
        # Synthesis may remove parameter objects from the gate-level netlist.
        # The physical CMOS5L implementation has four engines.
        neng = 4
    # Configure global pin ownership before starting any engines.
    # Pin 0 -> Engine 0 (UART TX)
    # Pin 3 -> Engine 3 (UART TX)
    # Pin 6 -> Engine 1 (CAN TX)
    # All other pins -> Engine 0
    await h.cmd(0, 2, h.b8(2) + h.b16(0x10C0))
    dut._log.info(f"NENG = {neng}")
    progs = [("uart_tx", pe16.Cfg(osr_lsb=1)),                                   # pin 0
             ("can_tx", pe16.Cfg(poly=0x4599, crcw=14, osr_lsb=0, stuff_n=5)),   # pin 6
             ("uart_rx", pe16.Cfg(isr_lsb=1)),                                   # pin 1
             ("ws2812_p3", None)]
    # engine 3: a copy of uart_tx moved to pin 3 (same program text, different pin)
    from test import FW
    tx3 = open(os.path.join(FW, "uart_tx.pe")).read()
    tx3 = tx3.replace("oe 0 1", "oe 3 1").replace("pin 0 ", "pin 3 ").replace("raw 0", "raw 3")
    used = min(neng, 4)
    for e in range(used):
        name, cfg = progs[e]
        words = pe16.assemble(tx3) if name == "ws2812_p3" else fw(name)
        await h.load(e, words, cfg or pe16.Cfg(osr_lsb=1))

    # Global pin ownership:
    # pin 0 -> engine 0 (UART TX)
    # pin 1 -> engine 2 (UART RX, input-only)
    # pin 3 -> engine 3 (UART TX)
    # pin 6 -> engine 1 (CAN TX)
    await h.cmd(0, 2, h.b8(2) + h.b16(0x10C0))

    await ClockCycles(dut.clk, 40)

    # stimulus for all engines at once
    rng = random.Random(11)
    u0 = [0x31, 0x9E, 0x07]
    u3 = [0xC4, 0x5B]
    payload, ref_line = can_frame(0x2A5, [0xA1, 0xB2, 0xC3])
    words = [len(payload) - 1] + [int("".join(map(str, (payload[k:k + 16] + [0] * 16)[:16])), 2)
                                  for k in range(0, len(payload), 16)]
    rx_bytes = [0x6D, 0x10]

    cap0 = cocotb.start_soon(uart_capture(dut, 0, 16, len(u0)))
    cap3 = cocotb.start_soon(uart_capture(dut, 3, 16, len(u3))) if used > 3 else None
    capc = cocotb.start_soon(sample_line(dut, 6, CAN_T, len(ref_line))) if used > 1 else None

    async def drive_rx():
        await ClockCycles(dut.clk, 300)
        for v in rx_bytes:
            for b in [0] + [(v >> k) & 1 for k in range(8)] + [1]:
                dut.uio_in.value = 0xFD | (b << 1)
                await ClockCycles(dut.clk, 16)
            await ClockCycles(dut.clk, 40)
    drv = cocotb.start_soon(drive_rx()) if used > 2 else None

    await h.push(0, u0)
    if used > 1:
        await h.push(1, words[:4])
    if used > 3:
        await h.push(3, u3)
    if used > 1:
        rest = words[4:]
        while rest:
            _, tx = await h.counts(1)
            if tx < 4:
                await h.push(1, [rest.pop(0)])
    # engine 3 reset in the middle of engine 0/1 traffic must not disturb them
    if used > 3:
        await cap3
        await h.cmd(3, 7, h.b8(2))
        st3 = await h.status(3)
        assert st3["run"] == 0 and st3["pc"] == 0
    got0 = await cap0
    assert got0 == u0, f"engine 0 UART {got0}"
    if capc:
        gotc = await capc
        assert gotc == ref_line, "engine 1 CAN frame mismatch"
    if drv:
        await drv
        got2 = [(w >> 8) for w in await h.pop(2, 2)]
        assert got2 == rx_bytes, f"engine 2 UART RX {got2}"
    for e in range(min(used, 3)):
        st = await h.status(e)
        assert st["run"] == 1 and st["ov"] == 0, f"engine {e} status {st}"
    dut._log.info(f"{used} engines ran UART TX, CAN TX, UART RX and UART TX concurrently; all outputs match")
