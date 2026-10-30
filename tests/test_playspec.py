"""The Music Assembler playspec against the tune's own 6502 and the song grammar."""

import pytest

from pysidtracker.oracle import EmuPlayer
from pysidtracker.playspec import smt
from pysidtracker.playspec.tv import validate

from pymusicassembler import Player, parse, playspec
from pymusicassembler.player import MAX_CYCLES

FRAMES = 300
SPACE = "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid"
RAM = ((0, 0x100), (0x200, 0xD400), (0xD800, 0x10000))


def _tune(path):
    return parse(path.read_bytes())


def test_spec_memory_equals_the_6502(tune_path):
    """Every call leaves the 64 KiB outside the stack page and the SID/IO window
    as the tune's own play routine does."""
    song = _tune(tune_path)
    spec = Player(song)
    cpu = EmuPlayer(
        song.image,
        song.load_addr,
        song.init_addr,
        song.play_addr,
        max_cycles=MAX_CYCLES,
    )
    for frame in range(FRAMES):
        assert spec.play_frame() == cpu.play_frame(), frame
        mine, real = spec.memory, bytes(cpu._mem)  # pylint: disable=protected-access
        for lo, hi in RAM:
            assert mine[lo:hi] == real[lo:hi], (frame, hex(lo))


def test_streams_the_player_reads_parse_under_the_song_grammar(tune_path):
    """``validate=True`` parses each pattern and orderlist the spec opens."""
    song = _tune(tune_path)
    player = Player(song, validate=True)
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
