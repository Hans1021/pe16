import cocotb
from cocotb.triggers import ClockCycles

from test import setup


async def raw_transaction(h, bits):
    """Send an arbitrary number of SPI bits under one CS assertion."""
    h._set(cs=0, sck=0)
    await ClockCycles(h.dut.clk, 4)

    await h.xfer(bits)

    await ClockCycles(h.dut.clk, 4)
    h._set(cs=1, sck=0)
    await ClockCycles(h.dut.clk, 12)


@cocotb.test()
async def incomplete_transactions(dut):
    h = await setup(dut)

    # An incomplete command byte must have no effect.
    await raw_transaction(h, h.b8(0x07)[:5])
    assert (await h.status(0))["run"] == 0

    # Incomplete CTRL payload must not start the engine.
    await raw_transaction(h, h.b8(0x07) + h.b8(0x01)[:7])
    assert (await h.status(0))["run"] == 0

    # Incomplete TX word must not enter the FIFO.
    await raw_transaction(h, h.b8(0x03) + h.b16(0xA55A)[:15])
    assert await h.counts(0) == (0, 0)

    # Incomplete ownership CFG write must not change the register.
    await raw_transaction(
        h,
        h.b8(0x02) + h.b8(0x02) + h.b16(0xFFFF)[:15]
    )
    assert (await h.cmd(0, 8, nread=1))[0] == 0

    # Valid transactions must still work after all aborted transfers.
    await h.push(0, [0xA55A])
    assert await h.counts(0) == (0, 1)

    await h.cmd(0, 2, h.b8(2) + h.b16(0x10C0))
    assert (await h.cmd(0, 8, nread=1))[0] == 0x10C0


@cocotb.test()
async def unknown_opcode(dut):
    h = await setup(dut)

    # Unsupported opcodes must not change engine state or FIFO counts.
    for opcode in (0, 10, 15, 63):
        await h.cmd(0, opcode, h.b16(0xFFFF))

    assert (await h.status(0))["run"] == 0
    assert await h.counts(0) == (0, 0)
    assert (await h.cmd(0, 8, nread=1))[0] == 0

    # A valid command following invalid commands must still work.
    await h.push(0, [0x1234])
    assert await h.counts(0) == (0, 1)


@cocotb.test()
async def invalid_engine_id(dut):
    h = await setup(dut)
    try:
        neng = int(dut.user_project.NENG.value)
    except AttributeError:
        neng = 4

    if neng == 4:
        dut._log.info("All 2-bit engine IDs are valid; skipping invalid-ID checks")
        return

    # Make Engine 0 distinguishable from an invalid engine.
    await h.cmd(0, 7, h.b8(1))
    assert (await h.status(0))["run"] == 1

    for e in range(neng, 4):
        assert (await h.cmd(e, 5, nread=1))[0] == 0, (
            f"Invalid engine {e} returned nonzero status"
        )
        assert (await h.cmd(e, 6, nread=1))[0] == 0, (
            f"Invalid engine {e} returned nonzero FIFO counts"
        )

        # Invalid engine writes must not modify Engine 0.
        await h.cmd(e, 7, h.b8(0))
        assert (await h.status(0))["run"] == 1
