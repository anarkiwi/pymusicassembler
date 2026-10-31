"""Music Assembler song streams as the Lark grammar ``musicassembler.lark``.

Patterns, orderlists and arpeggio programs are the bytes the player's 8-bit
cursors walk from their pointer tables, one character per byte (latin-1);
:class:`ToModel` builds the typed model while the stream parses, under
:data:`GRAMMAR`, a :class:`pysidtracker.bytegrammar.ByteGrammar`.
"""

from typing import Iterator, List, Tuple

from lark import Token, Transformer, v_args
from pysidtracker.bytegrammar import ByteGrammar, Parsed, select_rule

from pymusicassembler import constants
from pymusicassembler.errors import MusicAssemblerParseError, SongValidationError
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    DurationEvent,
    InstrumentEvent,
    NoteEvent,
    OrderEntry,
    Orderlist,
)

PATTERN = "pattern"
ORDERLIST = "orderlist"
ORDERLIST_HOOK = "orderlist_hook"
ORDERLIST_INLINE = "orderlist_inline"
PROGRAM = "program"
PROGRAM_JUMP = "program_jump"
START = (PATTERN, ORDERLIST, ORDERLIST_HOOK, ORDERLIST_INLINE, PROGRAM, PROGRAM_JUMP)

# Orderlist entry ids each orderlist rule reads as commands, not patterns.
COMMANDS = {
    ORDERLIST: frozenset(),
    ORDERLIST_HOOK: frozenset({constants.ORDER_JUMP}),
    ORDERLIST_INLINE: frozenset({constants.ORDER_TEMPO, constants.ORDER_JUMP}),
}


def orderlist_rule(lay) -> str:
    """The orderlist start rule of the build ``lay`` (a player Layout)."""
    return select_rule(
        lay,
        (("order_tempo", ORDERLIST_INLINE), ("order_jump", ORDERLIST_HOOK)),
        ORDERLIST,
    )


def program_rule(lay) -> str:
    """The arpeggio-program start rule of the build ``lay``."""
    return select_rule(lay, (("program_jump", PROGRAM_JUMP),), PROGRAM)


def streams(song, lay) -> Iterator[Tuple[str, object, str]]:
    """``(name, stream, rule)`` for every orderlist, pattern and program of
    ``song`` stored for the build ``lay``."""
    for voice, orderlist in enumerate(song.orderlists):
        yield f"voice {voice} orderlist", orderlist, orderlist_rule(lay)
    for pid, pattern in enumerate(song.patterns):
        if pattern is not None:
            yield f"pattern {pid}", pattern, PATTERN
    for slot, program in enumerate(song.inner_programs):
        if program is not None:
            yield f"arpeggio {slot}", program, program_rule(lay)


def check(song, lay) -> None:
    """Raise :class:`SongValidationError` unless every stream of ``song``
    serializes to bytes the build ``lay`` reads back as the same stream."""
    GRAMMAR.check(streams(song, lay), SongValidationError, skip_equality=(PATTERN,))


def _dur(token: Token) -> int:
    return ord(token) & constants.CTL_DUR_MASK


@v_args(inline=True)
class ToModel(Transformer):
    """Grammar rules -> model: pattern events, an Orderlist, an Arpeggio."""

    # pylint: disable=missing-function-docstring  # one method per grammar rule

    def pattern(self, *rows):
        events = [event for row in rows[:-1] for event in row]
        return Parsed(events, rows[-1].end_pos)

    def read_on(self, _, *rows):
        hold = DurationEvent(constants.CTL_DUR_MASK, long=True)
        return Parsed([hold] + self.pattern(*rows).model, rows[-1].end_pos)

    def note(self, pitch, control):
        return [NoteEvent(ord(pitch), *control)]

    def plain(self, ctl):
        return ord(ctl), ()

    def vibrato(self, ctl, first, second):
        return ord(ctl), (ord(first), ord(second))

    filter = vibrato

    def rest(self, value):
        return [DurationEvent(_dur(value))]

    select_rest = rest

    def hold(self, value):
        return [DurationEvent(_dur(value), long=True)]

    def select(self, value, then):
        return [InstrumentEvent(ord(value) & 0x1F)] + then

    def entry(self, pattern_id, control):
        return OrderEntry.from_bytes(ord(pattern_id), ord(control))

    tempo = entry

    def jump(self, marker, target):
        return constants.ORDER_JUMP, self.entry(marker, target)

    def loop(self, _):
        return constants.ORDER_END, None

    def silence(self, marker):
        return constants.SILENCE_PATTERN, OrderEntry(pattern_id=ord(marker))

    def orderlist(self, *items):
        terminator, last = items[-1]
        entries: List[OrderEntry] = list(items[:-1]) + ([last] if last else [])
        model = Orderlist(entries=entries, terminator=terminator)
        return Parsed(model, len(model.to_bytes()))

    orderlist_hook = orderlist_inline = orderlist

    def program_row(self, wave, note, cutoff):
        return ArpeggioRow(wave=ord(wave), note=ord(note), filter=ord(cutoff))

    def row_jump(self, _, row):
        return constants.PROGRAM_JUMP, ord(row)

    def stop(self, marker):
        return ord(marker), None

    restart = stop

    def program(self, *items):
        terminator, jump = items[-1]
        model = Arpeggio(rows=list(items[:-1]), terminator=terminator, jump=jump)
        return Parsed(model, len(model.to_bytes()))

    program_jump = program


GRAMMAR = ByteGrammar(
    __package__, "musicassembler.lark", START, ToModel(), MusicAssemblerParseError
)
