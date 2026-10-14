"""Disassemble a range of a ``.sid``/``.prg`` image (player reverse-engineering aid).

    python scripts/disasm.py TUNE.sid [START [END]]

``START``/``END`` are hex C64 addresses (default: the whole loaded image).
"""

import sys

from jennings.devices.mpu6502 import MPU
from jennings.disassembler import Disassembler

from pysidtracker import SidImage


def disassemble(image: SidImage, start: int, end: int):
    """Yield ``(addr, bytes, text)`` for each instruction in ``[start, end)``."""
    mpu = MPU()
    mpu.memory[image.load : image.end] = image.image
    dis = Disassembler(mpu)
    pc = start
    while pc < end:
        length, text = dis.instruction_at(pc)
        yield pc, bytes(mpu.memory[pc : pc + length]), text
        pc += length


def main(argv=None):
    """Print the disassembly of the requested range."""
    argv = sys.argv[1:] if argv is None else argv
    with open(argv[0], "rb") as f:
        image = SidImage.from_bytes(f.read())
    start = int(argv[1], 16) if len(argv) > 1 else image.load
    end = int(argv[2], 16) if len(argv) > 2 else image.end
    for addr, raw, text in disassemble(image, start, end):
        print(f"{addr:04X}  {raw.hex(' '):<9} {text}")


if __name__ == "__main__":
    main()
