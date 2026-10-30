"""``SidFormat.spec``: the registered playspec and its standard note table."""

from dataclasses import replace

import pytest

from pysidtracker import parse_sid_header
from pysidtracker.errors import SidError
from pysidtracker.notes import frequency

from pymusicassembler import constants, playspec, reader
from pymusicassembler.formats import MUSIC_ASSEMBLER, Tune
from pymusicassembler.player import Player

from .test_corpus import corpus_tune  # noqa: F401 pylint: disable=unused-import
from .test_player import _native_order_hook

# pylint: disable=protected-access
CLOCK = constants.PAL_CLOCK_HZ
NTSC_CLOCK = 1022727
FRAMES = 60
FREE = 0xC000
NOTE_BASE = 0
PITCH_BASE = 0x80


def _poke(player, addr, data):
    for i, byte in enumerate(data):
        player._mem[addr + i] = byte & 0xFF


def _played(player):
    """``player`` and its playspec binding, voice 0 due to read a row."""
    found = playspec.binding(player.view(), player.entry)
    for voice in range(constants.VOICES):
        player._mem[found["dur"] + voice] = 0 if voice == 0 else 0x7F
    player._mem[found["tempo"]] = 0
    return found


def _note_words(player, note, transpose):
    """The ``(lo, hi, index)`` one note row of ``note`` under ``transpose`` leaves."""
    found = _played(player)
    _poke(player, FREE, (note, 0x00, constants.PATTERN_END))
    pid = player._mem[found["pattern_id"]]
    _poke(player, found["pattern_lo"] + pid, (FREE & 0xFF,))
    _poke(player, found["pattern_hi"] + pid, (FREE >> 8,))
    _poke(player, found["pat_cursor"], (0,))
    _poke(player, found["transrep"], (transpose << 4 | 1,))
    player.play_frame()
    mem = player._mem
    return mem[found["freq_lo_w"]], mem[found["freq_hi_w"]], mem[found["note_index"]]


def _program_words(player, note, held):
    """The ``(lo, hi)`` one program row of ``note`` leaves with ``held`` the note index."""
    found = _played(player)
    _poke(player, FREE, (0x41, note, 0, 0xFF))
    _poke(player, found["inner_lo"] + 1, (FREE & 0xFF,))
    _poke(player, found["inner_hi"] + 1, (FREE >> 8,))
    _poke(player, found["ctrl_mask"], (0xFF,))
    _poke(player, found["instr_cursor"], (0,))
    _poke(player, found["note_index"], (held,))
    for voice in range(constants.VOICES):
        mine = voice == 0
        _poke(player, found["note_ctl"] + voice, (constants.CTL_TRIGGERED,))
        _poke(player, found["flags"] + voice, (1 if mine else 0,))
        _poke(player, found["instr_x8"] + voice, (0,))
    _poke(player, found["sweep_ctr"], (0,))
    _poke(player, found["tempo"], (5,))
    player.play_frame()
    return player._mem[found["freq_lo_w"]], player._mem[found["freq_hi_w"]], found


def _native_word(player, found, index):
    mem = player._mem
    return mem[found["freq_lo"] + index], mem[found["freq_hi"] + index]


def _word(lo_hi):
    return lo_hi[0] | lo_hi[1] << 8


def test_spec_player_reproduces_the_player_grid(corpus_tune):  # noqa: F811
    song = reader.read(corpus_tune)
    if not song.play_addr:
        pytest.skip("an interrupt-hosted tune runs its own 6502, not the spec")
    expected = Player(song).render_grid(FRAMES)
    assert MUSIC_ASSEMBLER.spec_player(Tune(song)).render_grid(FRAMES) == expected


def test_spec_binds_the_models_own_image(tune_path):
    song = reader.read(tune_path)
    binding = MUSIC_ASSEMBLER.spec(Tune(song, 1))
    assert binding.image == song.image and binding.subtune == 1
    assert binding.psid and not playspec.has_native(binding.spec)
    assert binding.entries(Player(song).view()) == playspec.entries(Player(song).view())


@pytest.mark.parametrize(
    "note,transpose", [(0, 0), (37, 0), (95, 0), (57, 9), (95, 15)]
)
def test_standard_notes_are_the_equal_tempered_table_from_c0(
    tune_path, note, transpose
):
    tune = Tune(reader.read(tune_path))
    standard = _note_words(
        MUSIC_ASSEMBLER.spec_player(tune, notes=CLOCK), note, transpose
    )
    index = note + transpose
    assert standard[2] == index
    assert _word(standard[:2]) == frequency(index + NOTE_BASE, CLOCK)

    native = MUSIC_ASSEMBLER.spec_player(tune)
    lo_hi = _note_words(native, note, transpose)
    found = playspec.binding(native.view(), native.entry)
    assert lo_hi[:2] == _native_word(native, found, index)


@pytest.mark.parametrize(
    "note,held,absolute",
    [
        (0, 0, False),
        (7, 40, False),
        (12, 83, False),
        (0x80 | 30, 5, True),
        (0x7F, 60, False),
    ],
)
def test_program_notes_read_the_same_table(tune_path, note, held, absolute):
    tune = Tune(reader.read(tune_path))
    standard = MUSIC_ASSEMBLER.spec_player(tune, notes=CLOCK)
    lo, hi, found = _program_words(standard, note, held)
    skips = absolute and found["pitch_skip"]
    index = (note if skips else note + held) & 0x7F
    assert lo | hi << 8 == frequency(index + NOTE_BASE, CLOCK)

    native = MUSIC_ASSEMBLER.spec_player(tune)
    lo, hi, found = _program_words(native, note, held)
    assert (lo, hi) == _native_word(native, found, index)


def test_native_predicate_tells_the_hook_build_from_the_base_spec(
    tune_path, monkeypatch
):
    assert not playspec.has_native(playspec.SPEC)
    assert playspec.has_native(playspec.HOOK_SPEC)
    song = _native_order_hook(reader.read(tune_path))
    assert not playspec.has_native(MUSIC_ASSEMBLER.spec(Tune(song)).spec)

    def refuse(*_args, **_kwargs):
        raise SidError("not statable")

    monkeypatch.setattr(playspec, "unnative", refuse)
    assert playspec.has_native(MUSIC_ASSEMBLER.spec(Tune(song)).spec)


def _with_clock(song, clock):
    """``song`` whose PSID header says ``clock`` (1 PAL, 2 NTSC)."""
    head = bytearray(song.container_header)
    flags = int.from_bytes(head[0x76:0x78], "big") & ~0xC | clock << 2
    head[0x76:0x78] = flags.to_bytes(2, "big")
    return replace(song, container_header=bytes(head))


def test_spec_sid_is_the_tunes_own_container(tune_path):
    song = reader.read(tune_path)
    binding = MUSIC_ASSEMBLER.spec(Tune(song))
    assert binding.sid == tune_path.read_bytes()
    assert not MUSIC_ASSEMBLER.spec(Tune(replace(song, container_header=b""))).sid


@pytest.mark.parametrize("clock, hz", [(1, CLOCK), (2, NTSC_CLOCK)])
def test_standard_notes_follow_the_header_clock(tune_path, clock, hz):
    song = _with_clock(reader.read(tune_path), clock)
    assert parse_sid_header(song.container_header).is_ntsc == (clock == 2)
    player = MUSIC_ASSEMBLER.spec_player(Tune(song), notes=True)
    lo_hi = _note_words(player, 37, 0)
    assert _word(lo_hi[:2]) == frequency(lo_hi[2], hz)


def test_spec_player_without_a_header_plays_pal(tune_path):
    song = replace(reader.read(tune_path), container_header=b"")
    player = MUSIC_ASSEMBLER.spec_player(Tune(song), notes=True)
    lo_hi = _note_words(player, 37, 0)
    assert _word(lo_hi[:2]) == frequency(lo_hi[2], CLOCK)
