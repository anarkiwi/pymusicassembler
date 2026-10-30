"""docs/format.md's claims about the song format, shown on the grammar and the player.

Each test states one documented behaviour and observes it on the player's memory
or on the grammar, so a change to either that the docs do not follow fails here.
"""

import pytest

from pymusicassembler import constants, grammar, reader
from pymusicassembler.player import Player

from .test_player import FREE, _pattern, _program, rd, rows, variant, wr

PATTERN_ROWS = {
    "plain note": (0x10, 0x02),
    "vibrato note": (0x10, 0x22, 0x03, 0x01),
    "filter note": (0x10, 0x82, 0x4B, 0x07),
    "tied note": (0x10, 0x42),
    "rest": (0x62,),
    "hold": (0xA2,),
    "select, note": (0x81, 0x10, 0x02),
    "select, rest": (0x81, 0x65),
    "select, hold byte": (0x81, 0xA2),
}


@pytest.mark.parametrize("row", PATTERN_ROWS.values(), ids=PATTERN_ROWS)
def test_a_row_spans_the_bytes_the_grammar_parses(tune_path, row):
    """The player's cursor moves over exactly the bytes the pattern rule takes for
    a row, so the grammar and the play routine read a pattern alike."""
    data = row + (constants.PATTERN_END,)
    _, size = grammar.GRAMMAR.parse_prefix(bytes(data), grammar.PATTERN)
    assert size == len(data)
    player = Player(reader.read(tune_path))
    _pattern(player, row + data)
    rows(player, 0)
    assert rd(player, player.layout.pat_cursor) == len(row)


@pytest.mark.parametrize("ctl,retriggers", [(0x02, True), (0x42, False)])
def test_bit_six_of_the_control_byte_ties_the_note(tune_path, ctl, retriggers):
    """A tied note keeps the instrument program running instead of restarting it."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (0x10, ctl, constants.PATTERN_END))
    rows(player, 0)
    wr(player, lay.instr_cursor, 6)
    wr(player, lay.tempo, 5)
    player.play_frame()
    assert (rd(player, lay.instr_cursor) == 0) == retriggers


@pytest.mark.parametrize("byte", [0x65, 0x7F, 0xA2, 0xFF])
def test_a_rest_byte_after_a_select_gates_off(tune_path, byte):
    """Unlike a bare $A0-$FF hold, any $60-$FF after an instrument select is a rest."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (0x81, byte, 0x10, 0x02, constants.PATTERN_END))
    wr(player, lay.ctrl, 0x41)
    rows(player, 0)
    assert rd(player, lay.ctrl) == 0x40
    assert rd(player, lay.dur) == byte & constants.CTL_DUR_MASK


def test_the_silence_entry_parks_the_voice_for_good(tune_path):
    """A $FE orderlist entry sets the silence pattern, which gates off and never reads on."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    wr(player, lay.pattern_id, constants.SILENCE_PATTERN)
    wr(player, lay.order_cursor, 0)
    wr(player, lay.ctrl, 0x41)
    for _ in range(4):
        rows(player, 0)
        assert rd(player, lay.pattern_id) == constants.SILENCE_PATTERN
        assert rd(player, lay.order_cursor) == 0
        assert rd(player, lay.ctrl) == 0x40


def test_a_program_jump_marker_is_wave_data_on_a_build_without_it(tune_path):
    """$FD r ends a wave program only where the player tests for it."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    assert not lay.program_jump
    _program(
        player,
        (0x41, 0, 0, 0xFD, 0, 0, constants.PROGRAM_RESTART),
        instr_cursor=3,
    )
    assert rd(player, lay.ctrl) == 0xFD
    assert rd(player, lay.instr_cursor) == 0


@pytest.mark.parametrize("target,cursor", [(2, 6), (3, 0), (4, 0)])
def test_a_program_jump_restarts_from_three_past_the_build_bound(
    later_tune_path, target, cursor
):
    """r continues at byte 3r unless r is 0 or at least three past the build's
    bound cell, however long the program is."""
    player = variant(reader.read(later_tune_path), program_jump=FREE + 0x20)
    lay = player.layout
    wr(player, FREE + 0x20, 0)
    rows_before_marker = (0x41, 0x80, 0) * 3
    _program(
        player,
        rows_before_marker + (0xFD, target),
        instr_cursor=6,
    )
    assert rd(player, lay.instr_cursor) == cursor


def test_init_rewrites_only_the_work_window_and_the_reloaded_cells(tune_path):
    """Init zeroes ``$1081-$1090`` and reloads transpose and repeat per voice; every
    other work byte keeps its loaded value."""
    song = reader.read(tune_path)
    lay = Player(song).layout
    mem = Player(song).memory
    reloaded = {lay.transrep + v for v in range(constants.VOICES)} | {
        lay.repeat + v for v in range(constants.VOICES)
    }
    allowed = set(range(0x1081, 0x1091)) | reloaded | {lay.filter_owner}
    changed = {a for a, b in enumerate(song.image, song.load_addr) if mem[a] != b}
    assert changed <= allowed
