"""The Music Assembler playspec against the tune's own 6502 and the song grammar."""

import pytest

from pysidtracker.oracle import EmuPlayer
from pysidtracker.playspec import smt
from pysidtracker.playspec.tv import validate
from pysidtracker.testing import song_frames

from pymusicassembler import Player, parse, playspec
from pymusicassembler.player import MAX_CYCLES

FRAMES = 300  # a stream-validation window, not a gate
SPACE = "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid"
FILTER_REGS = 0x15  # the first register past the three voices
RAM = ((0, 0x100), (0x200, 0xD400), (0xD800, 0x10000))


def _tune(path):
    return parse(path.read_bytes())


def assert_memory_equals_the_6502(data):
    """After every call of the tune's whole song the SID registers and the 64 KiB
    outside the stack page and the SID/IO window equal the tune's own play
    routine's. A resonance stored to a SID mirror (Crumb's ``$D430``, which the
    layout folds to ``$D410``) is not a register of the CPU run's, so its
    voice-3 register is not compared."""
    assert_song_equals_the_6502(parse(data), song_frames(data, None))


def assert_song_equals_the_6502(song, frames):
    """:func:`assert_memory_equals_the_6502` over ``frames`` calls of ``song``."""
    spec = Player(song)
    folded = spec.layout.res_sta - spec.layout.sid
    skip = folded if folded < FILTER_REGS else None
    cpu = EmuPlayer(
        song.image,
        song.load_addr,
        song.init_addr,
        song.play_addr,
        max_cycles=MAX_CYCLES,
    )
    for frame in range(frames):
        spec.play_frame()
        cpu.play_frame()
        for reg, (mine, real) in enumerate(zip(spec.regs, cpu.regs)):
            assert reg == skip or mine == real, (frame, reg)
        mine, real = spec.memory, bytes(cpu._mem)  # pylint: disable=protected-access
        for lo, hi in RAM:
            assert mine[lo:hi] == real[lo:hi], (frame, hex(lo))


def test_spec_memory_equals_the_6502(tune_path):
    assert_memory_equals_the_6502(tune_path.read_bytes())


def test_streams_the_player_reads_parse_under_the_song_grammar(tune_path):
    """``validate=True`` parses each pattern and orderlist the spec opens."""
    song = _tune(tune_path)
    player = Player(song, validate=True)
    player.render_grid(FRAMES)
    assert player.validator.results
    assert not player.validator.failures()


def test_later_build_streams_parse_under_their_orderlist_dialect(later_tune_path):
    """``$FC t`` and ``$FD n`` are commands of the rule the build selects."""
    player = Player(_tune(later_tune_path), validate=True)
    player.render_grid(FRAMES)
    assert player.validator.results
    assert not player.validator.failures()


@pytest.mark.oracle
def test_row_reader_is_proven_against_the_6502(tune):
    """``voice[0].parse`` equals the 6502 row reader for all initial memory."""
    player = Player(_tune(tune(SPACE)))
    view = player.view()
    found = playspec.binding(view, player.entry)
    result = validate(
        playspec.SPEC,
        found,
        "voice[0].parse",
        view,
        playspec.row_reader(view, found),
        registers={"X": 0},
        preserve=("X",),
    )
    assert result.proven, result.counterexample and {
        k: v for k, v in result.counterexample.items() if k != "memory"
    }


@pytest.mark.oracle
def test_tempo_invariant_holds_from_post_init_memory(tune):
    player = Player(_tune(tune(SPACE)))
    found = playspec.binding(player.view(), player.entry)
    mem = smt.concrete_memory(
        player.view(), symbolic=(found["tempo"], found["tempo_reload"])
    )
    assert smt.bmc(player.program(), 2, mem).holds
