
import cocotb
from cocotb.triggers import ClockCycles

from test import setup
import pe16


def pin_state(dut):
    value = int(dut.uio_out.value) & 1
    enable = int(dut.uio_oe.value) & 1
    return value, enable


async def load_pin_program(host, engine, value, enable=1):
    program = pe16.assemble(f"""
        set oe 0 {enable}
        set pin 0 {value}
        halt
    """)
    await host.load(engine, program, pe16.Cfg())


async def set_owner(host, engine):
    # Global CFG register at address 2.
    # Bits [1:0] control ownership of pin 0.
    await host.cmd(0, 2, host.b8(2) + host.b16(engine))

async def get_owner(host):
    return (await host.cmd(0, 8, nread=1))[0]

@cocotb.test()
async def pin_ownership(dut):
    h = await setup(dut)
    assert await get_owner(h) == 0x0000

    # Engine 0 drives LOW; Engine 1 drives HIGH.
    await load_pin_program(h, 0, 0)
    await load_pin_program(h, 1, 1)
    await ClockCycles(dut.clk, 20)

    # Reset ownership defaults to Engine 0.
    assert pin_state(dut) == (0, 1), (
        f"Default ownership failed: {pin_state(dut)}"
    )

    # Transfer pin 0 to Engine 1.
    await set_owner(h, 1)
    await ClockCycles(dut.clk, 5)

    assert await get_owner(h) == 0x0001

    assert pin_state(dut) == (1, 1), (
        f"Ownership transfer failed: {pin_state(dut)}"
    )

    # Resetting Engine 1 must NOT transfer ownership to Engine 0.
    await h.cmd(1, 7, h.b8(2))
    await ClockCycles(dut.clk, 5)

    assert await get_owner(h) == 0x0001

    assert pin_state(dut)[1] == 0, (
        "Engine reset unexpectedly enabled another engine's output"
    )

    # Engine 1 owns the pin, but disables its output.
    # Engine 0 is still driving LOW internally.
    await load_pin_program(h, 1, 1, enable=0)
    await ClockCycles(dut.clk, 20)

    assert pin_state(dut)[1] == 0, (
        "Non-owner engine is driving an unenabled pin"
    )

    # Re-enable Engine 1's output.
    await load_pin_program(h, 1, 1)
    await ClockCycles(dut.clk, 20)

    assert pin_state(dut) == (1, 1)

    # Restore ownership to Engine 0.
    await set_owner(h, 0)
    await ClockCycles(dut.clk, 5)

    assert pin_state(dut) == (0, 1), (
        "Transfer back to Engine 0 failed"
    )

    # Global reset should restore default ownership.
    await set_owner(h, 1)
    await ClockCycles(dut.clk, 5)
    assert pin_state(dut) == (1, 1)

    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    assert await get_owner(h) == 0x0000
    assert int(dut.uio_oe.value) == 0x00

    # Re-enable all pins for the existing ownership test.
    await h.cmd(0, 2, h.b8(3) + h.b16(0x00FF))

    await load_pin_program(h, 0, 0)
    await load_pin_program(h, 1, 1)
    await ClockCycles(dut.clk, 20)

    assert pin_state(dut) == (0, 1), (
        "Global reset did not restore Engine 0 ownership"
    )

    dut._log.info("Explicit pin ownership: all checks passed")


@cocotb.test()
async def all_pin_ownership(dut):
    h = await setup(dut)
    try:
        neng = int(dut.user_project.NENG.value)
    except AttributeError:
        neng = 4

    # Each engine drives a different 8-bit pattern.
    patterns = [0x00, 0xFF, 0xAA, 0x55]

    for e in range(neng):
        program = pe16.assemble(f"""
            set oes 255
            set pins {patterns[e]}
            halt
        """)
        await h.load(e, program, pe16.Cfg())

    await ClockCycles(dut.clk, 20)

    # Assign each pin independently to an engine.
    for offset in range(neng):
        owner_word = 0
        expected_out = 0

        for p in range(8):
            owner = (p + offset) % neng
            owner_word |= owner << (2 * p)
            expected_out |= ((patterns[owner] >> p) & 1) << p

        await h.cmd(0, 2, h.b8(2) + h.b16(owner_word))
        await ClockCycles(dut.clk, 5)

        assert await get_owner(h) == owner_word

        actual_out = int(dut.uio_out.value)
        actual_oe = int(dut.uio_oe.value)

        assert actual_out == expected_out, (
            f"Ownership {owner_word:04X}: "
            f"output {actual_out:02X}, expected {expected_out:02X}"
        )
        assert actual_oe == 0xFF, (
            f"Output enable {actual_oe:02X}, expected FF"
        )

    # In configurations with fewer than four engines,
    # selecting a nonexistent owner must disable the pin.
    if neng < 4:
        await h.cmd(0, 2, h.b8(2) + h.b16(0xFFFF))
        await ClockCycles(dut.clk, 5)

        assert int(dut.uio_oe.value) == 0x00, (
            "Invalid engine ownership enabled an output"
        )

    dut._log.info("All eight pins: ownership checks passed")
