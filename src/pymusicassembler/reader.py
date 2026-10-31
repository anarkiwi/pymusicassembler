"""Read a Music Assembler tune (PSID/.sid or .prg image) into a Song.

Table bases come from the player code's own operands (see
:mod:`pymusicassembler.layout`); an unrecognized player raises a parse error.
"""

from typing import Iterable, List, Optional, Tuple

from pysidtracker import (
    PSID_MAGIC,
    RSID_MAGIC,
    BaseSidParser,
    Match,
    SidError,
    SidImage,
    load_prefix,
    read_bytes,
    require_ram,
)

from pymusicassembler import constants, grammar, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.player import Player
from pymusicassembler.model import (
    Arpeggio,
    Instrument,
    InitState,
    InstrumentEvent,
    Orderlist,
    Pattern,
    PresetBank,
    Song,
)


def _parse_container(
    data: bytes,
) -> Tuple[int, int, int, str, str, str, bytes, bytes]:
    """Return (load, init, play, name, author, released, image, header).

    ``header`` is the exact container bytes that precede ``image`` (the
    PSID/RSID header + any embedded load-address prefix), kept so the
    writer can re-emit a byte-identical container.  The container is
    unwrapped with :class:`pysidtracker.SidImage`, whose ``container`` /
    ``image`` split is byte-identical to the raw file.
    """
    try:
        img = SidImage.from_bytes(data)
    except SidError as exc:  # normalise to this package's parse error
        raise MusicAssemblerParseError(str(exc)) from exc
    header = img.header
    load = img.load
    if header is not None:
        name = header.name
        author = header.author
        released = header.released
        init = header.init_address or load
        play = header.play_address
    else:  # bare .prg
        name = author = released = ""
        init = constants.DEFAULT_INIT
        play = constants.DEFAULT_PLAY
    return load, init, play, name, author, released, img.image, img.container


def discover_layout(image: SidImage, play: int, start: int, end: int) -> layout.Layout:
    """The :class:`~pymusicassembler.layout.Layout` of the player ``play`` reaches.

    Every table base must lie in ``[start, end)``; raises
    :class:`MusicAssemblerParseError` otherwise or when the player is not found.
    """
    entry = layout.resolve_entry(image, play, start, end)
    if entry is None:
        raise MusicAssemblerParseError("no play entry found; unsupported variant")
    lay = layout.discover(image, entry, start, end)
    for name, base in lay.tables().items():
        if not start <= base < end:
            raise MusicAssemblerParseError(
                f"discovered {name} base {base:#06x} outside image "
                f"[{start:#06x},{end:#06x}); unsupported variant"
            )
    return lay


def load_view(image: bytes, load: int) -> SidImage:
    """Absolute-addressed read view of ``image`` placed at ``load``.

    Uses :class:`pysidtracker.SidImage`; ``peek`` returns 0 out of range,
    matching the reader's reliance on a zero fallback.
    """
    return SidImage.from_prg(load_prefix(load) + image)


def _play_entry(image: SidImage, load: int, image_len: int) -> Optional[Match]:
    """The native-layout play entry, whose tempo cell is ``base + $90``.

    A genuine native image loads at ``base`` or ``base + $21``.
    """
    end = load + image_len
    for match in layout.play_entries(image, load, end):
        base = match.captures["tempo"] - constants.NATIVE_TEMPO_OFFSET
        if load - base in (0, constants.NATIVE_PLAY_OFFSET):
            return match
    return None


def find_player_base(image: SidImage, load: int, image_len: int) -> Optional[int]:
    """Locate the native player base address, or ``None`` for another layout."""
    match = _play_entry(image, load, image_len)
    if match is None:
        return None
    return match.captures["tempo"] - constants.NATIVE_TEMPO_OFFSET


def parse_orderlist(
    img: SidImage, lay, voice: int, rule: str = grammar.ORDERLIST
) -> Orderlist:
    """Voice ``voice``'s orderlist, read with the build's orderlist ``rule``.

    It ends at its own terminator (``$FF``, a single-byte ``$FE`` silence, or
    the build's ``$FD n`` jump), never reading the next list stored after it.
    """
    what = f"voice {voice} orderlist"
    return grammar.GRAMMAR.entry(
        img, lay.order_lo, lay.order_hi, voice, rule, what
    ).model


def _decode_pattern(img: SidImage, lay, pattern_id: int):
    """``(Pattern, events)`` of pattern ``pattern_id``, read with the pattern grammar."""
    addr = img.ptr(lay.pattern_lo, lay.pattern_hi, pattern_id)
    what = f"pattern {pattern_id}"
    events, size = grammar.GRAMMAR.read(img, addr, grammar.PATTERN, what)
    return Pattern(data=list(img.window(addr, size - 1))), events


def parse_pattern(img: SidImage, lay, pattern_id: int) -> Pattern:
    """Pattern ``pattern_id``'s rows, read with the pattern grammar."""
    return _decode_pattern(img, lay, pattern_id)[0]


def stored_program(img: SidImage, lay, slot: int, region: range) -> Optional[Arpeggio]:
    """The program ``slot`` points at in ``region``, else ``None``.

    The editor leaves unused slots as placeholder pointers, often shared and
    often outside the image; a slot is a program only when its bytes are one.
    """
    rule = grammar.program_rule(lay)
    return grammar.GRAMMAR.stored(img, lay.inner_lo, lay.inner_hi, slot, rule, region)


def _referenced_pattern_ids(orderlists: List[Orderlist], commands=()) -> List[int]:
    skip = {constants.SILENCE_PATTERN, *commands}
    return sorted({e.pattern_id for o in orderlists for e in o.entries} - skip)


def _stored_pattern(
    img: SidImage, lay, pattern_id: int, referenced: bool, extent: range
):
    """``(Pattern, events)``, else ``(None, [])`` for an id no orderlist plays
    whose pointer reaches no pattern in the file's ``extent`` (the player never
    reads it)."""
    if (
        not referenced
        and img.ptr(lay.pattern_lo, lay.pattern_hi, pattern_id) not in extent
    ):
        return None, []
    try:
        return _decode_pattern(img, lay, pattern_id)
    except MusicAssemblerParseError:
        if referenced:
            raise
        return None, []


def _selected_instruments(event_lists: Iterable[list]) -> List[int]:
    """Instrument ids the pattern grammar's ``select`` rows name."""
    return sorted(
        {
            event.instrument
            for events in event_lists
            for event in events
            if isinstance(event, InstrumentEvent)
        }
    )


def _stored_patterns(img: SidImage, lay: "layout.Layout", end: int) -> int:
    """Patterns the editor's pointer table stores, else 0.

    The editor saves ``pattern_hi`` then ``pattern_lo``, each holding one
    pointer per stored pattern (referenced or not) and a closing zero entry,
    all inside the file (a rip may cut the table short).
    """
    count = lay.pattern_lo - lay.pattern_hi - 1
    if not 0 < count < end - lay.pattern_lo:
        return 0
    if img.ptr(lay.pattern_lo, lay.pattern_hi, count):
        return 0
    ptrs = (img.ptr(lay.pattern_lo, lay.pattern_hi, i) for i in range(count))
    return count if all(lay.order_hi < ptr < end for ptr in ptrs) else 0


def _record(img: SidImage, addr: int, end: int) -> Instrument:
    """The instrument record at ``addr``; bytes at and past the file's ``end``
    (0: no file extent) read as zero, as the file stores no more. A record a
    pattern selects is read whole from the machine: the player reads the RAM
    there, whatever the file stores."""
    room = min(constants.INSTR_RECORD_SIZE, (end or 0x10000) - addr)
    return Instrument.from_bytes(img.window(addr, max(room, 0)))


def _stored_instruments(lay: "layout.Layout", end: int, data: Iterable[int]) -> int:
    """Preset records an instrument table placed last holds, up to the next ``data``.

    The editor saves its presets up to the image end, so the final record may
    be partial; data stored past them (an appended program) bounds the table.
    """
    if lay.instr < max(lay.tables().values()) or lay.instr >= end:
        return 0
    end = min([end] + [addr for addr in data if addr > lay.instr])
    size = constants.INSTR_RECORD_SIZE
    return min(-(-(end - lay.instr) // size), constants.INSTR_COUNT)


def _locate(
    image: bytes,
    load: int,
    init: Optional[int],
    play: int,
    subtune: int = 0,
    header: bytes = b"",
    boot: bool = False,
):
    """``(view, layout, post_init, player)``: the loaded image, else memory after
    init of 0-based ``subtune``; with ``boot`` the memory after init always, with
    the layout of the play entry that init leaves.

    ``post_init`` is true when the loaded image's layout is not that one.
    ``init`` ``None`` (a bare image) allows only the static view and boots no
    ``player``.
    """
    img = load_view(image, load)
    lay = None
    try:
        lay = discover_layout(img, play, load, load + len(image))
    except MusicAssemblerParseError:
        if init is None:
            raise
    if init is None or (lay and not boot):
        return img, lay, False, None
    song = Song(
        load_addr=load,
        init_addr=init,
        play_addr=play,
        image=image,
        container_header=header,
    )
    player = Player(song, subtune)
    post_init = lay != player.layout
    return load_view(player.view(), 0), player.layout, post_init, player


def parse(data: bytes, subtune: int = 0) -> Song:
    """Parse Music Assembler tune bytes (PSID/.sid or .prg) into a Song.

    The tables are read from memory after the tune's init runs in the emulator
    for 0-based ``subtune``: the orderlists the voices point at, the patterns
    those reach and the instruments and programs in use, as that init left them
    (a subtune's init may repoint the voices; packed or relocating rips and rips
    whose init selects one of several players per subtune place their tables
    there). :attr:`Song.init_state` holds that memory.
    """
    load, init, play, name, author, released, image, header = _parse_container(data)
    require_ram(load, len(image), "tune image", MusicAssemblerParseError)
    sid = data[:4] in (PSID_MAGIC, RSID_MAGIC)
    img, lay, post_init, player = _locate(
        image, load, init if sid else None, play, subtune, header, boot=True
    )
    if not sid:
        init, play = lay.init, lay.entry

    rule = grammar.orderlist_rule(lay)
    orderlists = [
        parse_orderlist(img, lay, voice, rule) for voice in range(constants.VOICES)
    ]

    extent = range(load, load + len(image))
    end = extent.stop if all(b in extent for b in lay.tables().values()) else 0
    if not end:  # no file extent
        extent = range(0x10000)
    pattern_ids = _referenced_pattern_ids(orderlists, grammar.COMMANDS[rule])
    npat = max([pid + 1 for pid in pattern_ids] + [_stored_patterns(img, lay, end)])
    decoded = [
        _stored_pattern(img, lay, pid, pid in pattern_ids, extent)
        for pid in range(npat)
    ]
    patterns = [pattern for pattern, _ in decoded]

    home = min(load, lay.entry - constants.NATIVE_PLAY_OFFSET)
    region = range(home, max(home, load) + len(image))
    inner_programs = [
        stored_program(img, lay, slot, region) for slot in range(constants.ARP_COUNT)
    ]
    data = [
        img.ptr(lay.inner_lo, lay.inner_hi, i)
        for i, p in enumerate(inner_programs)
        if p
    ]
    data += [
        img.ptr(lay.pattern_lo, lay.pattern_hi, pid)
        for pid, pattern in enumerate(patterns)
        if pattern is not None
    ]
    data += [
        img.ptr(lay.order_lo, lay.order_hi, voice) for voice in range(constants.VOICES)
    ]
    instr_ids = _selected_instruments(events for _, events in decoded)
    reached = max([iid + 1 for iid in instr_ids], default=0)
    ninstr = max(reached, _stored_instruments(lay, end, data))
    rec = constants.INSTR_RECORD_SIZE
    instruments = [
        _record(img, lay.instr + iid * rec, 0 if iid < reached else end)
        for iid in range(ninstr)
    ]
    freq_lo = list(img.window(lay.freq_lo, constants.FREQ_TABLE_LEN))
    freq_hi = list(img.window(lay.freq_hi, constants.FREQ_TABLE_LEN))

    song = Song(
        load_addr=load,
        init_addr=init,
        play_addr=play,
        name=name,
        author=author,
        released=released,
        orderlists=orderlists,
        patterns=patterns,
        instruments=instruments,
        freq_lo=freq_lo,
        freq_hi=freq_hi,
        inner_programs=inner_programs,
        image=image,
        container_header=header,
        layout=lay,
        layout_post_init=post_init,
        layout_subtune=subtune,
    )
    player = player or Player(song, subtune)
    song.init_state = InitState(subtune, player.view(), player.entry)
    return song


def read(src, subtune: int = 0) -> Song:
    """Read a Music Assembler tune from a path, bytes, or file-like object."""
    return parse(read_bytes(src), subtune)


def parse_presets(data: bytes) -> PresetBank:
    """Parse a ``P.`` preset bank file into a :class:`PresetBank`.

    Accepts the editor's 768-byte body at ``$4300`` with or without its
    two-byte load-address prefix; any other size is not a preset bank.
    """
    body = data
    if len(body) == constants.PRESET_BANK_BODY + 2:
        body = body[2:]
    if len(body) != constants.PRESET_BANK_BODY:
        raise MusicAssemblerParseError(
            f"preset bank must be {constants.PRESET_BANK_BODY} bytes"
            f" (the editor's $4300 bank, with or without its load prefix),"
            f" got {len(data)}"
        )
    return PresetBank.from_bytes(body)


def read_presets(src) -> PresetBank:
    """Read a ``P.`` preset bank from a path, bytes, or file-like object."""
    return parse_presets(read_bytes(src))


class MusicAssemblerSidParser(BaseSidParser):
    """:class:`pysidtracker.BaseSidParser` adapter for the shared API.

    ``parse``/``read`` yield the same :class:`~pymusicassembler.model.Song`
    as the module-level :func:`parse`/:func:`read`.  ``recognize`` reports the
    player statically: it returns the order-signature offset when all four
    table-load instruction signatures are present in the loaded image, so
    ``detect`` classifies a direct-load Music Assembler tune as
    ``PlayroutineKind.DIRECT`` (and packed/relocating variants once init runs).
    """

    error_class = MusicAssemblerParseError

    def parse(self, data: bytes, **kwargs) -> Song:
        return parse(data, kwargs.get("subtune", 0))

    def recognize(self, image: SidImage) -> object:
        """The play-entry address if a whole MA player is in the loaded region.

        A PSID/RSID whose init relocates the player is located in memory after
        init, as :func:`parse` does.
        """
        header = image.header
        play = header.play_address if header else 0
        try:
            return discover_layout(
                image, play or image.load, image.load, image.end
            ).entry
        except MusicAssemblerParseError:
            pass
        init = header.init_address if header else 0
        if not init:
            return None
        try:
            return _locate(image.image, image.load, init, play or image.load)[1].entry
        except Exception:  # pylint: disable=broad-except
            # init may fail or not terminate; an unlocatable player is unrecognised
            return None
