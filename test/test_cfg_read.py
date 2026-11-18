
import cocotb
from test import setup


async def read_cfg(h, engine, addr):
    return (await h.cmd(
        engine, 9, h.b8(addr), nread=1
    ))[0]


@cocotb.test()
async def configuration_readback(dut):
    h = await setup(dut)

    # Check defaults.
    assert await read_cfg(h, 0, 2) == 0
    assert await read_cfg(h, 0, 3) == 0x00FF

    # Global ownership.
    await h.cmd(0, 2, h.b8(2) + h.b16(0x10C0))
    assert await read_cfg(h, 0, 2) == 0x10C0

    # Validity mask.
    await h.cmd(0, 2, h.b8(3) + h.b16(0x0089))
    assert await read_cfg(h, 0, 3) == 0x0089

    # Engine-specific CRC configuration.
    await h.cmd(1, 2, h.b8(0) + h.b16(0xA55A))
    assert await read_cfg(h, 1, 0) == 0xA55A
    assert await read_cfg(h, 0, 0) == 0

    # Engine-specific miscellaneous configuration.
    await h.cmd(1, 2, h.b8(1) + h.b16(0x0ABC))
    assert await read_cfg(h, 1, 1) == 0x0ABC

    # Engine-specific reset preserves configuration.
    await h.cmd(1, 7, h.b8(2))
    assert await read_cfg(h, 1, 0) == 0xA55A
    assert await read_cfg(h, 1, 1) == 0x0ABC

    # Restore all pins to the unassigned state.
    await h.cmd(0, 2, h.b8(3) + h.b16(0))
    assert int(dut.uio_oe.value) == 0

    dut._log.info("Configuration readback and pin mask passed")
