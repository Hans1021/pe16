
import cocotb
from cocotb.triggers import ClockCycles

from test import setup
import pe16


async def program_engine(h, engine, value):
    """Reset, reprogram, and start an engine."""
    program = pe16.assemble(f"""
        set oe 0 1
        set pin 0 {value}
        halt
    """)
    await h.load(engine, program, pe16.Cfg())
    await ClockCycles(h.dut.clk, 20)


@cocotb.test()
async def repeated_reprogramming(dut):
    h = await setup(dut)

    for i in range(10):
        value = i & 1

        await program_engine(h, 0, value)

        status = await h.status(0)
        assert status["halt"] == 1
        assert status["pc"] == 2
        assert int(dut.uio_oe.value) & 1
        assert (int(dut.uio_out.value) & 1) == value

        # Stop and reset the engine.
        await h.cmd(0, 7, h.b8(0))
        await h.cmd(0, 7, h.b8(2))
        await ClockCycles(dut.clk, 10)

        status = await h.status(0)
        assert status["run"] == 0
        assert status["halt"] == 0
        assert status["pc"] == 0
        assert (int(dut.uio_oe.value) & 1) == 0

    dut._log.info("Repeated reprogramming: 10 cycles passed")


@cocotb.test()
async def independent_engine_reset(dut):
    h = await setup(dut)

    # Engine 0 owns pin 0; Engine 1 owns pin 1.
    await h.cmd(0, 2, h.b8(2) + h.b16(0x0004))

    program0 = pe16.assemble("""
        set oe 0 1
        set pin 0 1
        halt
    """)

    program1 = pe16.assemble("""
        set oe 1 1
        set pin 1 1
        halt
    """)

    await h.load(0, program0, pe16.Cfg())
    await h.load(1, program1, pe16.Cfg())
    await ClockCycles(dut.clk, 20)

    assert (int(dut.uio_out.value) & 3) == 3
    assert (int(dut.uio_oe.value) & 3) == 3

    # Reset only Engine 0.
    await h.cmd(0, 7, h.b8(2))
    await ClockCycles(dut.clk, 10)

    # Engine 0 releases pin 0.
    assert (int(dut.uio_oe.value) & 1) == 0

    # Engine 1 must remain unaffected.
    assert (int(dut.uio_out.value) & 2) == 2
    assert (int(dut.uio_oe.value) & 2) == 2

    status1 = await h.status(1)
    assert status1["run"] == 1
    assert status1["halt"] == 1

    # Global ownership remains unchanged.
    owner = (await h.cmd(0, 8, nread=1))[0]
    assert owner == 0x0004

    dut._log.info("Independent engine reset passed")
