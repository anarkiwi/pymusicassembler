"""Music Assembler song streams as the Lark grammar ``musicassembler.lark``.

Patterns, orderlists and arpeggio programs are the bytes the player's 8-bit
cursors walk from their pointer tables, one character per byte (latin-1);
:class:`ToModel` builds the typed model while the stream parses.
"""

from functools import lru_cache
from importlib import resources
from typing import Iterator, List, NamedTuple, Tuple, Union

from lark import Lark, Token, Transformer, Tree, v_args
from lark.exceptions import LarkError

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

REACH = 0x100  # the player indexes every stream with an 8-bit Y cursor

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


class Parsed(NamedTuple):
    """A parsed stream: its model and the bytes it spans, terminator included."""

    model: Union[list, Orderlist, Arpeggio]
    size: int


@lru_cache(maxsize=None)
def parser(trees: bool = False) -> Lark:
    """The LALR parser over every stream: :class:`Parsed` results, else trees."""
    text = resources.files(__package__).joinpath("musicassembler.lark").read_text()
    return Lark(
        text,
        parser="lalr",
        lexer="contextual",
        start=list(START),
        transformer=None if trees else ToModel(),
    )


def orderlist_rule(lay) -> str:
    """The orderlist start rule of the build ``lay`` (a player Layout)."""
    if lay.order_tempo:
        return ORDERLIST_INLINE
    return ORDERLIST_HOOK if lay.order_jump else ORDERLIST


def program_rule(lay) -> str:
    """The arpeggio-program start rule of the build ``lay``."""
    return PROGRAM_JUMP if lay.program_jump else PROGRAM


def _parse(data: bytes, rule: str, trees: bool = False):
    try:
        return parser(trees).parse(bytes(data).decode("latin-1"), start=rule)
    except LarkError as exc:
        raise MusicAssemblerParseError(
            f"{rule} has no terminator within {len(data)} bytes"
        ) from exc


def parse_prefix(data: bytes, rule: str) -> Parsed:
    """The ``rule`` stream at the start of ``data``; the rest is never read."""
    return _parse(data, rule)


def parse_exact(data: bytes, rule: str):
    """The model of ``data``, which must be exactly one ``rule`` stream."""
    model, size = _parse(data, rule)
    if size != len(data):
        raise MusicAssemblerParseError(
            f"{rule} ends after {size} of its {len(data)} bytes"
        )
    return model


def tree(data: bytes, rule: str) -> Tree:
    """The parse tree of the ``rule`` stream at the start of ``data``."""
    return _parse(data, rule, trees=True)


def _byte(token: Token) -> str:
    return f"{token.type}=${ord(token):02x}"


def render(node: Tree, depth: int = 0) -> Iterator[str]:
    """Indented lines of ``node`` in stream order; a rule holding only bytes
    is one line of ``TERMINAL=$xx`` items."""
    pad = "  " * depth
    if not any(isinstance(child, Tree) for child in node.children):
        yield " ".join([pad + node.data] + [_byte(tok) for tok in node.children])
        return
    yield pad + node.data
    for child in node.children:
        if isinstance(child, Tree):
            yield from render(child, depth + 1)
        else:
            yield pad + "  " + _byte(child)


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
    for name, stream, rule in streams(song, lay):
        try:
            back = parse_exact(stream.to_bytes(), rule)
        except MusicAssemblerParseError as exc:
            raise SongValidationError(
                f"{name} is not one {rule} stream: {exc}"
            ) from exc
        if rule != PATTERN and back != stream:
            raise SongValidationError(f"{name} reads back as {back}, not {stream}")


def _dur(token: Token) -> int:
    return ord(token) & constants.CTL_DUR_MASK


@v_args(inline=True)
class ToModel(Transformer):
    """Grammar rules -> model: pattern events, an Orderlist, an Arpeggio."""

    # pylint: disable=missing-function-docstring  # one method per grammar rule

    def pattern(self, *rows):
        events = [event for row in rows[:-1] for event in row]
        return Parsed(events, rows[-1].end_pos)

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
