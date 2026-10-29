"""The Lark stream grammar: patterns, orderlists, arpeggio programs."""

from types import SimpleNamespace

import pytest
from pysidtracker.bytegrammar import render

from pymusicassembler import constants, grammar, reader
from pymusicassembler.errors import MusicAssemblerParseError, SongValidationError
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    DurationEvent,
    InstrumentEvent,
    NoteEvent,
    OrderEntry,
    Orderlist,
    Pattern,
    Song,
)

OLD = SimpleNamespace(order_tempo=0, order_jump=0, program_jump=0)
HOOK = SimpleNamespace(order_tempo=0, order_jump=0x1234, program_jump=0)
LATER = SimpleNamespace(order_tempo=0x1234, order_jump=0x1234, program_jump=0x1234)


@pytest.mark.parametrize(
    "data,events,size",
    [
        ((0x10, 0x05, 0xFF), [NoteEvent(0x10, 0x05)], 3),
        ((0x10, 0x45, 0xFF), [NoteEvent(0x10, 0x45)], 3),
        # vibrato steps are two bytes; an $FF step is data, not the end.
        ((0x18, 0x21, 0xFF, 0x00, 0xFF), [NoteEvent(0x18, 0x21, (0xFF, 0x00))], 5),
        ((0x18, 0x61, 0x10, 0xFF, 0xFF), [NoteEvent(0x18, 0x61, (0x10, 0xFF))], 5),
        # filter: cutoff and sweep length follow, either may be $FF.
        ((0x18, 0x83, 0x4F, 0xFF, 0xFF), [NoteEvent(0x18, 0x83, (0x4F, 0xFF))], 5),
        ((0x18, 0xFF, 0xFF, 0xFF, 0xFF), [NoteEvent(0x18, 0xFF, (0xFF, 0xFF))], 5),
        ((0x63, 0xFF), [DurationEvent(3)], 2),
        ((0xA4, 0xFF), [DurationEvent(4, long=True)], 2),
        ((0xFE, 0xFF), [DurationEvent(0x1E, long=True)], 2),
        (
            (0x82, 0x10, 0x05, 0xFF),
            [InstrumentEvent(2), NoteEvent(0x10, 0x05)],
            4,
        ),
        ((0x83, 0x62, 0xFF), [InstrumentEvent(3), DurationEvent(2)], 3),
        # after a select any byte from $60 (hold range, select range, $FF) rests.
        ((0x81, 0xA5, 0xFF), [InstrumentEvent(1), DurationEvent(5)], 3),
        ((0x81, 0x85, 0xFF), [InstrumentEvent(1), DurationEvent(5)], 3),
        ((0x81, 0xFF, 0xFF), [InstrumentEvent(1), DurationEvent(0x1F)], 3),
        ((0xFF,), [], 1),
    ],
)
def test_pattern_rows(data, events, size):
    """Each row kind decodes to its events and the pattern spans ``size`` bytes."""
    assert grammar.GRAMMAR.parse_prefix(bytes(data) + b"\x10\x05", grammar.PATTERN) == (
        events,
        size,
    )


def test_pattern_ends_at_the_first_row_start_ff():
    """Bytes after the row-start $FF are another stream's."""
    parsed = grammar.GRAMMAR.parse_prefix(
        bytes((0x63, 0xFF, 0x10, 0x05, 0xFF)), "pattern"
    )
    assert parsed == ([DurationEvent(3)], 2)


@pytest.mark.parametrize(
    "data", [(0x10, 0x05), (0x10, 0xFF), (0x18, 0x21, 0x00, 0xFF), (0x82, 0xFF)]
)
def test_unterminated_pattern_is_rejected(data):
    """A $FF read as a control byte, argument or select rest ends no pattern."""
    with pytest.raises(MusicAssemblerParseError, match="no terminator within"):
        grammar.GRAMMAR.parse_prefix(bytes(data), grammar.PATTERN)


def test_orderlist_takes_fc_and_fd_as_pattern_ids():
    """The original build plays $FC and $FD as patterns; only $FF/$FE end it."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0xFC, 0x00, 0xFD, 0x12, 0xFF, 0x07)), grammar.ORDERLIST
    )
    assert [e.pattern_id for e in model.entries] == [0xFC, 0xFD]
    assert model.entries[1] == OrderEntry(0xFD, transpose=1, repeat=2)
    assert model.terminator == constants.ORDER_END
    assert size == 5


def test_orderlist_hook_ends_on_a_jump():
    """The hook build's $FD n ends the list; $FC is still a pattern id."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0xFC, 0x00, 0xFD, 0x03, 0x01, 0x00, 0xFF)), grammar.ORDERLIST_HOOK
    )
    assert model.terminator == constants.ORDER_JUMP
    assert [e.pattern_id for e in model.entries] == [0xFC, constants.ORDER_JUMP]
    assert model.entries[-1].control_byte() == 0x03
    assert size == 4


def test_orderlist_inline_tempo_continues_the_list():
    """The later build's $FC t is an entry; the list goes on to its terminator."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0xFC, 0x06, 0x01, 0x20, 0xFD, 0x01, 0x02)), grammar.ORDERLIST_INLINE
    )
    assert [(e.pattern_id, e.control_byte()) for e in model.entries] == [
        (constants.ORDER_TEMPO, 0x06),
        (0x01, 0x20),
        (constants.ORDER_JUMP, 0x01),
    ]
    assert model.terminator == constants.ORDER_JUMP
    assert size == 6


@pytest.mark.parametrize("rule", grammar.START[1:4])
def test_silence_is_one_byte(rule):
    """$FE parks the voice: the next voice's bytes are not part of the list."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0x01, 0x00, 0xFE, 0x35, 0x00)), rule
    )
    assert model == Orderlist(
        [OrderEntry(1), OrderEntry(constants.SILENCE_PATTERN)],
        constants.SILENCE_PATTERN,
    )
    assert size == 3


def test_program_reads_fd_as_wave():
    """Without the jump command $FD is a row's waveform byte."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0xFD, 0x10, 0x00, 0xFF, 0x41)), grammar.PROGRAM
    )
    assert model == Arpeggio([ArpeggioRow(0xFD, 0x10, 0x00)])
    assert size == 4


def test_program_jump_ends_on_fd():
    """The later build's row-start $FD r continues at row r."""
    model, size = grammar.GRAMMAR.parse_prefix(
        bytes((0x41, 0xFF, 0xFE, 0xFD, 0x00, 0x11)), grammar.PROGRAM_JUMP
    )
    assert model == Arpeggio(
        [ArpeggioRow(0x41, 0xFF, 0xFE)], constants.PROGRAM_JUMP, jump=0
    )
    assert size == 5


@pytest.mark.parametrize("rule", (grammar.PROGRAM, grammar.PROGRAM_JUMP))
def test_program_stops_on_fe(rule):
    model, size = grammar.GRAMMAR.parse_prefix(bytes((0x41, 0, 0, 0xFE, 0x99)), rule)
    assert model.terminator == constants.PROGRAM_STOP
    assert size == 4


def test_parse_exact_rejects_trailing_bytes():
    with pytest.raises(MusicAssemblerParseError, match="ends after 3 of its 4 bytes"):
        grammar.GRAMMAR.parse_exact(bytes((0x01, 0x00, 0xFF, 0x00)), grammar.ORDERLIST)


def test_parse_exact_rejects_unterminated_input():
    with pytest.raises(MusicAssemblerParseError, match="no terminator within 2"):
        grammar.GRAMMAR.parse_exact(bytes((0x01, 0x00)), grammar.ORDERLIST)


def test_parse_exact_returns_the_model():
    assert grammar.GRAMMAR.parse_exact(bytes((0x41, 0, 0, 0xFF)), grammar.PROGRAM) == (
        Arpeggio([ArpeggioRow(0x41, 0, 0)])
    )


def test_render_follows_stream_order():
    """Rules nest by indent; tokens appear in byte order with their terminal."""
    data = bytes((0x82, 0x10, 0x05, 0x63, 0x18, 0x83, 0x4F, 0xFF, 0xFF, 0x00))
    assert list(render(grammar.GRAMMAR.tree(data, grammar.PATTERN))) == [
        "pattern",
        "  select",
        "    INSTRUMENT=$82",
        "    note",
        "      NOTE=$10",
        "      plain CONTROL=$05",
        "  rest REST=$63",
        "  note",
        "    NOTE=$18",
        "    filter CONTROL_FILTER=$83 BYTE=$4f BYTE=$ff",
        "  PATTERN_END=$ff",
    ]


@pytest.mark.parametrize(
    "data,rule,lines",
    [
        ((0xFF, 0x01), grammar.PATTERN, ["pattern PATTERN_END=$ff"]),
        ((0xFF,), grammar.ORDERLIST, ["orderlist", "  loop ORDER_LOOP=$ff"]),
        (
            (0xFD, 0x02),
            grammar.PROGRAM_JUMP,
            ["program_jump", "  row_jump PROGRAM_JUMP=$fd BYTE=$02"],
        ),
    ],
)
def test_render_bytes_only_rule_on_one_line(data, rule, lines):
    assert list(render(grammar.GRAMMAR.tree(bytes(data), rule))) == lines


@pytest.mark.parametrize(
    "lay,orderlist,program",
    [
        (OLD, grammar.ORDERLIST, grammar.PROGRAM),
        (HOOK, grammar.ORDERLIST_HOOK, grammar.PROGRAM),
        (LATER, grammar.ORDERLIST_INLINE, grammar.PROGRAM_JUMP),
        (
            SimpleNamespace(order_tempo=0, order_jump=0, program_jump=1),
            grammar.ORDERLIST,
            grammar.PROGRAM_JUMP,
        ),
    ],
)
def test_rules_follow_the_layout(lay, orderlist, program):
    assert grammar.orderlist_rule(lay) == orderlist
    assert grammar.program_rule(lay) == program


def _song(**kwargs):
    return Song(
        orderlists=kwargs.pop("orderlists", [Orderlist([OrderEntry(0)])] * 3),
        **kwargs,
    )


def test_streams_skip_unread_patterns():
    song = _song(patterns=[None, Pattern([0x10, 0x05])])
    song.inner_programs[3] = Arpeggio()
    names = [name for name, _, _ in grammar.streams(song, OLD)]
    assert names == [
        "voice 0 orderlist",
        "voice 1 orderlist",
        "voice 2 orderlist",
        "pattern 1",
        "arpeggio 3",
    ]
    grammar.check(song, OLD)


def test_check_rejects_a_mid_list_silence():
    """A $FE entry before the end parks the voice there: the rest is unread."""
    bad = Orderlist([OrderEntry(constants.SILENCE_PATTERN), OrderEntry(1)])
    song = _song(orderlists=[Orderlist([OrderEntry(0)])] * 2 + [bad])
    with pytest.raises(SongValidationError, match="voice 2 orderlist is not one"):
        grammar.check(song, OLD)


def test_check_rejects_a_pattern_cut_short():
    song = _song(patterns=[Pattern([0x63, 0xFF, 0x10, 0x05])])
    with pytest.raises(SongValidationError, match="pattern 0 is not one pattern"):
        grammar.check(song, OLD)


def test_check_rejects_a_program_that_reads_back_differently():
    """A jump with no target row is stored as row 0 and reads back so."""
    song = _song()
    song.inner_programs[0] = Arpeggio(terminator=constants.PROGRAM_JUMP)
    with pytest.raises(SongValidationError, match="arpeggio 0 reads back as"):
        grammar.check(song, LATER)


def test_check_rejects_a_jump_the_build_lacks():
    """Without the program jump command $FD r is half a wave row."""
    song = _song()
    song.inner_programs[0] = Arpeggio(terminator=constants.PROGRAM_JUMP, jump=0)
    with pytest.raises(SongValidationError, match="arpeggio 0 is not one program"):
        grammar.check(song, OLD)


def test_check_passes_tunes(tune_path):
    song = reader.read(tune_path)
    grammar.check(song, song.layout)


@pytest.mark.parametrize("fixture", ["later_tune_path", "jump_tune_path"])
def test_check_passes_later_builds(fixture, request):
    song = reader.read(request.getfixturevalue(fixture))
    assert grammar.orderlist_rule(song.layout) != grammar.ORDERLIST
    grammar.check(song, song.layout)
