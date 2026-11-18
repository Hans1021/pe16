
import cocotb
from cocotb.triggers import ClockCycles

from test import setup
import pe16


@cocotb.test()
async def tx_fifo_full_and_empty(dut):
    h = await setup(dut)

    # Echo TX words into RX, one at a time.
    program = pe16.assemble("""
    top:
        pull
        mov isr osr
        push
        jmp top
    """)

    # Load firmware, but stop the engine before filling TX.
    await h.load(0, program, pe16.Cfg())
    await h.cmd(0, 7, h.b8(2))

    words = [0x1234, 0x5678, 0x9ABC, 0xDEF0]

    # Fill the four-entry FIFO.
    await h.push(0, words)
    assert await h.counts(0) == (0, 4)

    # A fifth write must not corrupt existing entries.
    await h.push(0, [0xFFFF])
    assert await h.counts(0) == (0, 4)

    # Start engine and let it echo all four words.
    await h.cmd(0, 7, h.b8(1))
    await ClockCycles(dut.clk, 100)

    assert await h.counts(0) == (4, 0), (
        f"Unexpected counts: {await h.counts(0)}"
    )

    received = await h.pop(0, 4)
    assert received == words, (
        f"FIFO order corrupted: {received} != {words}"
    )

    assert await h.counts(0) == (0, 0)

    # Reading an empty FIFO returns zero.
    assert await h.pop(0) == [0]
    assert await h.counts(0) == (0, 0)


@cocotb.test()
async def rx_fifo_full_stall_resume(dut):
    h = await setup(dut)

    # Generate five distinct RX words.
    # PUSH should stall on the fifth word until host reads.
    program = pe16.assemble("""
        set x 101
        mov isr x
        push

        set x 202
        mov isr x
        push

        set x 303
        mov isr x
        push

        set x 404
        mov isr x
        push

        set x 505
        mov isr x
        push

        halt
    """)

    await h.load(0, program, pe16.Cfg())
    await ClockCycles(dut.clk, 100)

    assert await h.counts(0) == (4, 0), (
        "RX FIFO should hold exactly four words"
    )

    status = await h.status(0)
    assert status["halt"] == 0, (
        "Engine should stall at the fifth PUSH, not HALT"
    )

    # Reading one entry creates space for the stalled PUSH.
    assert await h.pop(0) == [101]

    await ClockCycles(dut.clk, 30)

    assert await h.counts(0) == (4, 0)

    remaining = await h.pop(0, 4)
    assert remaining == [202, 303, 404, 505], (
        f"RX ordering failed: {remaining}"
    )

    assert await h.counts(0) == (0, 0)

    await ClockCycles(dut.clk, 10)
    assert (await h.status(0))["halt"] == 1


@cocotb.test()
async def reset_clears_fifos(dut):
    h = await setup(dut)

    # Fill TX without running any firmware.
    await h.push(0, [0x1111, 0x2222, 0x3333])
    assert await h.counts(0) == (0, 3)

    # Reset only Engine 0.
    await h.cmd(0, 7, h.b8(2))
    await ClockCycles(dut.clk, 10)

    assert await h.counts(0) == (0, 0)
    assert await h.pop(0) == [0]

    # A different engine's FIFO must be unaffected.
    await h.push(1, [0xABCD])
    await h.push(0, [0x1234])

    await h.cmd(0, 7, h.b8(2))
    await ClockCycles(dut.clk, 10)

    assert await h.counts(0) == (0, 0)
    assert await h.counts(1) == (0, 1)
