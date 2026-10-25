"""Read a Music Assembler tune (PSID/.sid or .prg image) into a Song.

Table bases come from the player code's own operands (see
:mod:`pymusicassembler.layout`); an unrecognized player raises a parse error.
"""

from typing import List, Optional, Tuple

from pysidtracker import (
    PSID_MAGIC,
    RSID_MAGIC,
    BaseSidParser,
    CodePattern,
    Match,
    SidError,
    SidImage,
    find_code_all,
    read_bytes,
)

from pymusicassembler import constants, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.player import Player
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    Instrument,
    OrderEntry,
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


_TABLES = (
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


def image_in_io_area(load: int, length: int) -> bool:
    """True when ``[load, load+length)`` intersects the C64 I/O area $D000-$DFFF."""
    start, end = constants.IO_AREA
    return load < end and load + length > start


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


def _parse_orderlist(
    img: SidImage, bases: dict, voice: int, jump: Optional[int] = None
) -> Orderlist:
    """Voice ``voice``'s orderlist, up to and including its on-disk terminator.

    Ends at ``$FF``, the single-byte ``$FE`` silence (the player parks on it
    and never reads past), or a ``$FD n`` jump; it reads only the terminator's
    own bytes, never the next voice's list that the editor packs after it.
    """
    addr = img.ptr(bases["order_lo"], bases["order_hi"], voice)
    entries: List[OrderEntry] = []
    terminator = constants.ORDER_END
    for cur in range(0, constants.ORDER_MAX_BYTES, 2):
        pid = img.peek(addr + cur)
        if pid == constants.ORDER_END:
            break
        if pid == constants.SILENCE_PATTERN:
            entries.append(OrderEntry(pattern_id=pid))
            terminator = constants.SILENCE_PATTERN
            break
        ctl = img.peek(addr + cur + 1)
        entries.append(OrderEntry.from_bytes(pid, ctl))
        if jump is not None and pid == jump:
            terminator = constants.ORDER_JUMP
            break
    else:
        # No terminator within the bound: a wrong base or unsupported variant.
        raise MusicAssemblerParseError(
            f"orderlist for voice {voice} at {addr:#06x} has no terminator "
            f"within {constants.ORDER_MAX_BYTES} bytes; unsupported variant"
        )
    return Orderlist(entries=entries, terminator=terminator)


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


def _parse_program(img: SidImage, addr: int, limit: int) -> Arpeggio:
    """One arpeggio program at ``addr``: its rows, then the terminating marker.

    Walks like the player: a row is three bytes (wave, note, filter); a
    row-start byte of $FF/$FE/$FD is the terminator (a $FD carries the
    row to continue at).
    """
    rows: List[ArpeggioRow] = []
    cur = 0
    end = min(limit, addr + constants.ARP_MAX_BYTES)
    while addr + cur < end:
        marker = img.peek(addr + cur)
        if marker in (
            constants.PROGRAM_JUMP,
            constants.PROGRAM_STOP,
            constants.PROGRAM_RESTART,
        ):
            break
        rows.append(
            ArpeggioRow(
                wave=img.peek(addr + cur),
                note=img.peek(addr + cur + 1),
                filter=img.peek(addr + cur + 2),
            )
        )
        cur += constants.ARP_ROW_LEN
    else:
        raise MusicAssemblerParseError(
            f"arpeggio program at {addr:#06x} has no terminator within "
            f"{constants.ARP_MAX_BYTES} bytes; unsupported variant"
        )
    jump = img.peek(addr + cur + 1) if marker == constants.PROGRAM_JUMP else None
    return Arpeggio(rows=rows, terminator=marker, jump=jump)


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
                programs.append(_parse_program(img, ptr, limit))
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


def _referenced_instrument_ids(patterns: List[Pattern]) -> List[int]:
    ids = set()
    for pattern in patterns:
        for byte in pattern.data:
            if constants.INSTR_MIN <= byte <= constants.INSTR_MAX:
                ids.add(byte & 0x1F)
    return sorted(ids)


def _locate(image: bytes, load: int, init: Optional[int], play: int):
    """``(view, layout, static)``: the loaded image, else memory after the tune's init.

    ``static`` is False when the layout was only found by running init.

    ``init`` ``None`` (a bare image) allows only the static view.
    """
    img = load_view(image, load)
    try:
        return img, discover_layout(img, play, load, load + len(image)), True
    except MusicAssemblerParseError:
        if init is None:
            raise
    player = Player(Song(load_addr=load, init_addr=init, play_addr=play, image=image))
    return load_view(player.memory, 0), player.layout, False


def parse(data: bytes) -> Song:
    """Parse Music Assembler tune bytes (PSID/.sid or .prg) into a Song.

    A player whose tables lie outside the loaded image is read after its init
    runs in the emulator (packed or relocating rips).
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
    img, lay, static = _locate(image, load, init if sid else None, play)
    if not sid:
        init, play = lay.init, lay.entry
    bases = {name: getattr(lay, name) for name in _TABLES}
    commands = {constants.ORDER_TEMPO} if lay.order_tempo else set()
    commands |= {constants.ORDER_JUMP} if lay.order_jump else set()

    orderlists = [
        _parse_orderlist(
            img, bases, voice, constants.ORDER_JUMP if lay.order_jump else None
        )
        for voice in range(constants.VOICES)
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
    home = min(load, lay.entry - constants.NATIVE_PLAY_OFFSET)
    inner_programs = _parse_inner_programs(img, lay, home, max(home, load) + len(image))

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
        located_after_init=not static,
    )


def read(src) -> Song:
    """Read a Music Assembler tune from a path, bytes, or file-like object."""
    return parse(read_bytes(src))


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
