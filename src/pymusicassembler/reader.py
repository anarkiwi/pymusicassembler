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
    read_bytes,
)

from pymusicassembler import constants, grammar, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.player import Player
from pymusicassembler.model import (
    Arpeggio,
    Instrument,
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


TABLES = (
    "order_lo",
    "order_hi",
    "pattern_lo",
    "pattern_hi",
    "instr",
    "freq_lo",
    "freq_hi",
    "inner_lo",
    "inner_hi",
)


def discover_layout(image: SidImage, play: int, start: int, end: int) -> layout.Layout:
    """The :class:`~pymusicassembler.layout.Layout` of the player ``play`` reaches.

    Every table base must lie in ``[start, end)``; raises
    :class:`MusicAssemblerParseError` otherwise or when the player is not found.
    """
    entry = layout.resolve_entry(image, play, start, end)
    if entry is None:
        raise MusicAssemblerParseError("no play entry found; unsupported variant")
    lay = layout.discover(image, entry, start, end)
    for name in TABLES:
        base = getattr(lay, name)
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
    return SidImage.from_prg(bytes((load & 0xFF, load >> 8)) + image)


def image_in_io_area(load: int, length: int) -> bool:
    """True when ``[load, load+length)`` intersects the C64 I/O area $D000-$DFFF."""
    start, end = constants.IO_AREA
    return load < end and load + length > start


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


def window(img: SidImage, addr: int, size: int = grammar.REACH) -> bytes:
    """The ``size`` bytes from ``addr`` a player cursor can reach (to ``$FFFF``)."""
    return bytes(img.mem[addr : min(addr + size, len(img.mem))])


def parse_orderlist(
    img: SidImage, bases: dict, voice: int, rule: str = grammar.ORDERLIST
) -> Orderlist:
    """Voice ``voice``'s orderlist, read with the build's orderlist ``rule``.

    It ends at its own terminator (``$FF``, a single-byte ``$FE`` silence, or
    the build's ``$FD n`` jump), never reading the next list stored after it.
    """
    addr = img.ptr(bases["order_lo"], bases["order_hi"], voice)
    try:
        return grammar.parse_prefix(window(img, addr), rule).model
    except MusicAssemblerParseError as exc:
        raise MusicAssemblerParseError(
            f"voice {voice} orderlist at {addr:#06x}: {exc}; unsupported variant"
        ) from exc


def _decode_pattern(img: SidImage, bases: dict, pattern_id: int):
    """``(Pattern, events)`` of pattern ``pattern_id``, read with the pattern grammar."""
    addr = img.ptr(bases["pattern_lo"], bases["pattern_hi"], pattern_id)
    data = window(img, addr)
    try:
        events, size = grammar.parse_prefix(data, grammar.PATTERN)
    except MusicAssemblerParseError as exc:
        raise MusicAssemblerParseError(
            f"pattern {pattern_id} at {addr:#06x}: {exc}"
        ) from exc
    return Pattern(data=list(data[: size - 1])), events


def parse_pattern(img: SidImage, bases: dict, pattern_id: int) -> Pattern:
    """Pattern ``pattern_id``'s rows, read with the pattern grammar."""
    return _decode_pattern(img, bases, pattern_id)[0]


def parse_program(
    img: SidImage, addr: int, limit: int, rule: str = grammar.PROGRAM_JUMP
) -> Arpeggio:
    """The arpeggio program at ``addr`` (below ``limit``), read with ``rule``."""
    try:
        data = window(img, addr, min(limit - addr, grammar.REACH))
        return grammar.parse_prefix(data, rule).model
    except MusicAssemblerParseError as exc:
        raise MusicAssemblerParseError(
            f"arpeggio program at {addr:#06x}: {exc}; unsupported variant"
        ) from exc


def _parse_inner_programs(
    img: SidImage, lay: "layout.Layout", base: int, limit: int
) -> List[Optional[Arpeggio]]:
    """The 16 arpeggio programs; ``None`` where a slot holds a placeholder.

    The editor stores a tune's programs in this image and leaves the other
    slots as placeholder pointers, often shared and often outside the image;
    a slot is real only when its bytes terminate as a program.
    """
    programs: List[Optional[Arpeggio]] = []
    for i in range(constants.ARP_COUNT):
        ptr = img.ptr(lay.inner_lo, lay.inner_hi, i)
        if base <= ptr < limit:
            try:
                programs.append(
                    parse_program(img, ptr, limit, grammar.program_rule(lay))
                )
                continue
            except MusicAssemblerParseError:
                pass
        programs.append(None)
    return programs


def _referenced_pattern_ids(orderlists: List[Orderlist], commands=()) -> List[int]:
    skip = {constants.SILENCE_PATTERN, *commands}
    ids = set()
    for orderlist in orderlists:
        for entry in orderlist.entries:
            if entry.pattern_id not in skip:
                ids.add(entry.pattern_id)
    return sorted(ids)


def _stored_pattern(img: SidImage, bases: dict, pattern_id: int, referenced: bool):
    """``(Pattern, events)``, else ``(None, [])`` for an id no orderlist plays
    whose pointer reaches no pattern (the player never reads it)."""
    try:
        return _decode_pattern(img, bases, pattern_id)
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


def _stored_instruments(lay: "layout.Layout", end: int, data: Iterable[int]) -> int:
    """Preset records an instrument table placed last holds, up to the next ``data``.

    The editor saves its presets up to the image end, so the final record may
    be partial; data stored past them (an appended program) bounds the table.
    """
    if lay.instr < max(getattr(lay, name) for name in TABLES) or lay.instr >= end:
        return 0
    end = min([end] + [addr for addr in data if addr > lay.instr])
    size = constants.INSTR_RECORD_SIZE
    return min(-(-(end - lay.instr) // size), constants.INSTR_COUNT)


def _locate(image: bytes, load: int, init: Optional[int], play: int, subtune: int = 0):
    """``(view, layout, post_init)``: the loaded image, else memory after init
    of 0-based ``subtune``.

    ``init`` ``None`` (a bare image) allows only the static view.
    """
    img = load_view(image, load)
    try:
        return img, discover_layout(img, play, load, load + len(image)), False
    except MusicAssemblerParseError:
        if init is None:
            raise
    song = Song(load_addr=load, init_addr=init, play_addr=play, image=image)
    player = Player(song, subtune)
    return load_view(player.memory, 0), player.layout, True


def parse(data: bytes, subtune: int = 0) -> Song:
    """Parse Music Assembler tune bytes (PSID/.sid or .prg) into a Song.

    A player whose tables lie outside the loaded image is read after its init
    runs in the emulator for 0-based ``subtune`` (packed or relocating rips,
    or a rip whose init selects one of several players per subtune).
    """
    load, init, play, name, author, released, image, header = _parse_container(data)
    if image_in_io_area(load, len(image)):
        raise MusicAssemblerParseError(
            f"tune image [{load:#06x},{load + len(image):#06x}) reaches the C64 I/O "
            f"area {constants.IO_AREA[0]:#06x}-{constants.IO_AREA[1] - 1:#06x} "
            "(a KERNAL LOAD stores into the VIC/SID/CIA chips there, not RAM, so "
            "the player reads live I/O and the file cannot produce its stored music)"
        )
    sid = data[:4] in (PSID_MAGIC, RSID_MAGIC)
    img, lay, post_init = _locate(image, load, init if sid else None, play, subtune)
    if not sid:
        init, play = lay.init, lay.entry
    bases = {name: getattr(lay, name) for name in TABLES}
    commands = {constants.ORDER_TEMPO} if lay.order_tempo else set()
    commands |= {constants.ORDER_JUMP} if lay.order_jump else set()

    rule = grammar.orderlist_rule(lay)
    orderlists = [
        parse_orderlist(img, bases, voice, rule) for voice in range(constants.VOICES)
    ]

    end = load + len(image) if img.load == load else 0  # 0: no file extent
    pattern_ids = _referenced_pattern_ids(orderlists, commands)
    stored = _stored_patterns(img, lay, end)
    npat = max([pid + 1 for pid in pattern_ids] + [stored])
    decoded = [
        _stored_pattern(img, bases, pid, pid in pattern_ids) for pid in range(npat)
    ]
    patterns = [pattern for pattern, _ in decoded]

    home = min(load, lay.entry - constants.NATIVE_PLAY_OFFSET)
    inner_programs = _parse_inner_programs(img, lay, home, max(home, load) + len(image))
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
    stored = _stored_instruments(lay, end, data)
    ninstr = max([iid + 1 for iid in instr_ids] + [stored])
    instruments = [
        Instrument.from_bytes(
            bytes(
                img.peek(bases["instr"] + iid * constants.INSTR_RECORD_SIZE + k)
                for k in range(constants.INSTR_RECORD_SIZE)
            )
        )
        for iid in range(ninstr)
    ]

    freq_lo = [img.peek(bases["freq_lo"] + n) for n in range(constants.FREQ_TABLE_LEN)]
    freq_hi = [img.peek(bases["freq_hi"] + n) for n in range(constants.FREQ_TABLE_LEN)]

    return Song(
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
