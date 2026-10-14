"""Read a Music Assembler tune (PSID/.sid or .prg image) into a Song.

Table bases come from the player code's own operands (see
:mod:`pymusicassembler.layout`); a variant whose player is not found raises
:class:`MusicAssemblerParseError` rather than yielding garbage.
"""

from typing import List, Optional, Tuple

from pysidtracker import (
    MEM_SIZE,
    BaseSidParser,
    CodePattern,
    Match,
    SidError,
    SidImage,
    find_code_all,
    read_bytes,
    run_init,
)

from pymusicassembler import constants, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.model import (
    Instrument,
    OrderEntry,
    Orderlist,
    Pattern,
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


_TABLES = (
    "order_lo",
    "order_hi",
    "pattern_lo",
    "pattern_hi",
    "instr",
    "freq_lo",
    "freq_hi",
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
    for name in _TABLES:
        base = getattr(lay, name)
        if not start <= base < end:
            raise MusicAssemblerParseError(
                f"discovered {name} base {base:#06x} outside image "
                f"[{start:#06x},{end:#06x}); unsupported variant"
            )
    return lay


def discover_bases(image: SidImage, load: int, image_len: int, play=None) -> dict:
    """Per-tune table base addresses of the player in ``[load, load + image_len)``."""
    lay = discover_layout(image, load if play is None else play, load, load + image_len)
    return {name: getattr(lay, name) for name in _TABLES}


def load_view(image: bytes, load: int) -> SidImage:
    """Absolute-addressed read view of ``image`` placed at ``load``.

    Uses :class:`pysidtracker.SidImage`; ``peek`` returns 0 out of range,
    matching the reader's reliance on a zero fallback.
    """
    return SidImage.from_prg(bytes((load & 0xFF, load >> 8)) + image)


_PAT_PLAY = CodePattern("A2 00 CE {tempo:w} 30 0C 20 26")


def _play_entry(image: SidImage, load: int, image_len: int) -> Optional[Match]:
    """The native-layout play entry, whose tempo cell is ``base + $90``.

    A genuine native image loads at ``base`` or ``base + $21``.
    """
    end = load + image_len
    for match in find_code_all(image, _PAT_PLAY, start=load, end=end):
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


def _parse_orderlist(img: SidImage, bases: dict, voice: int) -> Orderlist:
    addr = img.ptr(bases["order_lo"], bases["order_hi"], voice)
    entries: List[OrderEntry] = []
    for cur in range(0, constants.ORDER_MAX_BYTES, 2):
        pid = img.peek(addr + cur)
        if pid == constants.ORDER_END:
            return Orderlist(entries=entries)
        ctl = img.peek(addr + cur + 1)
        entries.append(OrderEntry.from_bytes(pid, ctl))
    # No terminator within the bound: a wrong base or unsupported variant.
    raise MusicAssemblerParseError(
        f"orderlist for voice {voice} at {addr:#06x} has no terminator "
        f"within {constants.ORDER_MAX_BYTES} bytes; unsupported variant"
    )


def _parse_pattern(img: SidImage, bases: dict, pattern_id: int) -> Pattern:
    """Extract a pattern's raw bytes, walking the grammar so an inline
    filter/vibrato argument byte that happens to be ``$FF`` is not mistaken
    for the pattern terminator (the terminator is only an ``$FF`` read at a
    row-START position, matching the player's $1187 end check)."""
    addr = img.ptr(bases["pattern_lo"], bases["pattern_hi"], pattern_id)
    data: List[int] = []
    cur = 0
    limit = 0x1000  # defensive bound
    while cur < limit:
        val = img.peek(addr + cur)
        if val == constants.PATTERN_END:  # row-start terminator
            break
        if val >= constants.INSTR_MIN:
            if val >= constants.LONGDUR_MIN:  # long duration: single byte
                data.append(val)
                cur += 1
                continue
            data.append(val)  # instrument select; may continue into a note
            cur += 1
            nxt = img.peek(addr + cur)
            if nxt >= constants.DUR_MIN:  # instrument + bare/long duration
                data.append(nxt)
                cur += 1
                continue
            val = nxt
        elif val >= constants.DUR_MIN:  # bare duration: single byte
            data.append(val)
            cur += 1
            continue
        # a note (val < 0x60): note byte + control byte (+ inline args).
        data.append(val)  # note
        cur += 1
        ctl = img.peek(addr + cur)
        data.append(ctl)  # control byte
        cur += 1
        if ctl >= constants.CTL_FILTER:  # filter command: 2 inline arg bytes
            data.extend((img.peek(addr + cur), img.peek(addr + cur + 1)))
            cur += 2
        elif ctl & constants.CTL_VIBARP:  # vibrato/arp params: 2 inline bytes
            data.extend((img.peek(addr + cur), img.peek(addr + cur + 1)))
            cur += 2
    return Pattern(data=data)


def _referenced_pattern_ids(orderlists: List[Orderlist], commands=()) -> List[int]:
    skip = {constants.SILENCE_PATTERN, *commands}
    ids = set()
    for orderlist in orderlists:
        for entry in orderlist.entries:
            if entry.pattern_id not in skip:
                ids.add(entry.pattern_id)
    return sorted(ids)


def _referenced_instrument_ids(patterns: List[Pattern]) -> List[int]:
    ids = set()
    for pattern in patterns:
        for byte in pattern.data:
            if constants.INSTR_MIN <= byte <= constants.INSTR_MAX:
                ids.add(byte & 0x1F)
    return sorted(ids)


def _locate(data: bytes, image: bytes, load: int, play: int):
    """``(view, layout)``: the loaded image, else the image after emulated init."""
    img = load_view(image, load)
    try:
        return img, discover_layout(img, play, load, load + len(image))
    except MusicAssemblerParseError as static_error:
        try:
            sid = SidImage.from_bytes(data)
            run_init(sid)
        except SidError:
            raise static_error from None
        return sid, discover_layout(sid, play, 0, MEM_SIZE)


def parse(data: bytes) -> Song:
    """Parse Music Assembler tune bytes (PSID/.sid or .prg) into a Song.

    A player whose tables lie outside the loaded image is read after its init
    runs in the emulator (packed or relocating rips).
    """
    load, init, play, name, author, released, image, header = _parse_container(data)
    img, lay = _locate(data, image, load, play)
    if not header:
        init, play = lay.init, lay.entry
    bases = {name: getattr(lay, name) for name in _TABLES}
    commands = {constants.ORDER_TEMPO} if lay.order_tempo else set()
    commands |= {constants.ORDER_JUMP} if lay.order_jump else set()

    orderlists = [
        _parse_orderlist(img, bases, voice) for voice in range(constants.VOICES)
    ]

    pattern_ids = _referenced_pattern_ids(orderlists, commands)
    max_pid = max(pattern_ids) if pattern_ids else -1
    patterns = [_parse_pattern(img, bases, pid) for pid in range(max_pid + 1)]

    instr_ids = _referenced_instrument_ids(patterns)
    max_iid = max(instr_ids) if instr_ids else -1
    instruments = [
        Instrument.from_bytes(
            bytes(
                img.peek(bases["instr"] + iid * constants.INSTR_RECORD_SIZE + k)
                for k in range(constants.INSTR_RECORD_SIZE)
            )
        )
        for iid in range(max_iid + 1)
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
        image=image,
        container_header=header,
    )


def read(src) -> Song:
    """Read a Music Assembler tune from a path, bytes, or file-like object."""
    return parse(read_bytes(src))


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
        return parse(data)

    def recognize(self, image: SidImage) -> object:
        """The play-entry address if a whole MA player is in the loaded region."""
        play = image.header.play_address if image.header else 0
        try:
            return discover_layout(
                image, play or image.load, image.load, image.end
            ).entry
        except MusicAssemblerParseError:
            return None
