"""Synthetic songs against the play routine: the spec equals the 6502, and the
songs reach every instruction and branch direction of the 6502 that can run.

The 6502 is run under jennings' coverage recorder (``CoverageVM``), which records
the executed instructions and, for each branch, the directions taken.
"""

import pytest

from jennings.coverage import EXEC, FALLTHRU, TAKEN, CoverageMap, CoverageVM
from jennings.coverage.cfg import successors
import jennings.memory
from jennings.devices import mpu6502
from jennings.isa import is_branch
from pysidtracker.oracle import EmuPlayer

from pymusicassembler import Player
from pymusicassembler.player import MAX_CYCLES

from .synthetic import FRAMES, synthesize
from .test_playspec import assert_song_equals_the_6502

LUX = "DEMOS/0-9/3LUX_Intro.sid"

# Offsets from the play entry of what no input reaches, with the reason. Each is
# the direction of a branch the flag the instruction before it set decides
# (``BCS`` after a ``CMP`` ... ``STA``, ``BNE`` after a ``LDA #``), or the
# data that follows such a branch.
DEAD = {
    0x0BA: "taken-only",  # BCS after CMP #$A0 passed: always taken
    0x0BC: "unexec",  # the zero fill after it
    0x14B: "taken-only",  # BNE after LDA #$F0
    0x2EA: "taken-only",  # BCS after INC of the high byte on a carry
    0x33B: "taken-only",  # BCS after the same carry
    0x3FB: "taken-only",  # BEQ after LDY #0
}


@pytest.fixture
def song(tune):
    return synthesize(tune(LUX).read_bytes())


def test_synthetic_song_spec_equals_the_6502(song):
    assert_song_equals_the_6502(song, FRAMES)


def test_synthetic_song_runs_every_path_of_the_6502(song, monkeypatch):
    cov = CoverageMap()
    monkeypatch.setattr(jennings.memory, "NATIVE", False)
    monkeypatch.setattr(
        mpu6502.MPU, "VM", staticmethod(lambda mem: CoverageVM(mem, cov))
    )
    cpu = EmuPlayer(
        song.image,
        song.load_addr,
        song.init_addr,
        song.play_addr,
        max_cycles=MAX_CYCLES,
    )
    for _ in range(FRAMES):
        cpu.play_frame()

    layout = Player(song).layout
    memory = bytearray(0x10000)
    memory[song.load_addr : song.load_addr + len(song.image)] = song.image
    # Static control flow from init and play; the tables start at freq_lo.
    code, todo = set(), [layout.entry, song.init_addr]
    while todo:
        addr = todo.pop()
        if addr in code or addr >= layout.freq_lo:
            continue
        code.add(addr)
        flow, _, callee = successors(memory, addr)
        todo += [target for _, target in flow]
        todo += [callee] if callee is not None else []

    missed = {}
    for addr in sorted(code):
        flags = cov.flags[addr]
        if not flags & EXEC:
            missed[addr - layout.entry] = "unexec"
        elif is_branch(memory[addr]) and not flags & TAKEN:
            missed[addr - layout.entry] = "fall-only"
        elif is_branch(memory[addr]) and not flags & FALLTHRU:
            missed[addr - layout.entry] = "taken-only"
    assert missed == DEAD
