"""Read a Music Assembler tune (PSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The Music Assembler
player binary is identical across tunes; only its DATA differs, and each
pointer-table base address lives at a fixed offset in the player code as
a 16-bit instruction operand.  The reader reads those operands to
discover the per-tune table bases (relocation-safe -- no fixed-address
assumption), then follows them to extract the orderlists, patterns,
instruments and note-frequency tables.
"""

from pathlib import Path
from typing import List, Tuple

from pysidtracker import BaseSidParser, SidError, SidImage

from pymusicassembler import constants
from pymusicassembler.errors import SidParseError
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
        raise SidParseError(str(exc)) from exc
    header = img.header
    load = img.load
    if header is not None:
        name = header.name
        author = header.author
        released = header.released
        init = header.init_address or load
        play = header.play_address or load
    else:  # bare .prg
        name = author = released = ""
        init = constants.DEFAULT_INIT
        play = constants.DEFAULT_PLAY
    return load, init, play, name, author, released, img.image, img.container


def _op16(image: bytes, off: int) -> int:
    if off + 1 < len(image):
        return image[off] | (image[off + 1] << 8)
    raise SidParseError(f"operand offset {off:#x} past end of image")


def _discover_bases(image: bytes) -> dict:
    """Discover per-tune table base addresses from player-code operands."""
    return {
        "order_lo": _op16(image, constants.OP_ORDER_LO),
        "order_hi": _op16(image, constants.OP_ORDER_HI),
        "pattern_lo": _op16(image, constants.OP_PATTERN_LO),
        "pattern_hi": _op16(image, constants.OP_PATTERN_HI),
        "instr": _op16(image, constants.OP_INSTR),
        "freq_lo": _op16(image, constants.OP_FREQ_LO),
        "freq_hi": _op16(image, constants.OP_FREQ_HI),
        "inner_lo": _op16(image, constants.OP_INNER_LO),
        "inner_hi": _op16(image, constants.OP_INNER_HI),
        "arp": _op16(image, constants.OP_ARP),
    }


def _load_view(image: bytes, load: int) -> SidImage:
    """Absolute-addressed read view of ``image`` placed at ``load``.

    Uses :class:`pysidtracker.SidImage`; ``peek`` returns 0 out of range,
    matching the reader's reliance on a zero fallback.
    """
    return SidImage.from_prg(bytes((load & 0xFF, load >> 8)) + image)


def _parse_orderlist(img: SidImage, bases: dict, voice: int) -> Orderlist:
    addr = img.ptr(bases["order_lo"], bases["order_hi"], voice)
    entries: List[OrderEntry] = []
    cur = 0
    seen = set()
    while True:
        if cur in seen:  # defensive: never loop forever on a malformed list
            break
        seen.add(cur)
        pid = img.peek(addr + cur)
        if pid == constants.ORDER_END:
            break
        ctl = img.peek(addr + cur + 1)
        entries.append(OrderEntry.from_bytes(pid, ctl))
        cur += 2
    return Orderlist(entries=entries)


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


def _referenced_pattern_ids(orderlists: List[Orderlist]) -> List[int]:
    ids = set()
    for orderlist in orderlists:
        for entry in orderlist.entries:
            if entry.pattern_id != constants.SILENCE_PATTERN:
                ids.add(entry.pattern_id)
    return sorted(ids)


def _referenced_instrument_ids(patterns: List[Pattern]) -> List[int]:
    ids = set()
    for pattern in patterns:
        for byte in pattern.data:
            if constants.INSTR_MIN <= byte <= constants.INSTR_MAX:
                ids.add(byte & 0x1F)
    return sorted(ids)


def parse(data: bytes) -> Song:
    """Parse Music Assembler tune bytes (PSID/.sid or .prg) into a Song."""
    load, init, play, name, author, released, image, header = _parse_container(data)
    bases = _discover_bases(image)
    img = _load_view(image, load)

    orderlists = [
        _parse_orderlist(img, bases, voice) for voice in range(constants.VOICES)
    ]

    pattern_ids = _referenced_pattern_ids(orderlists)
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
    if isinstance(src, bytes):
        return parse(src)
    if isinstance(src, (str, Path)):
        return parse(Path(src).read_bytes())
    if hasattr(src, "read"):
        return parse(src.read())
    raise TypeError(f"cannot read a song from {type(src).__name__}")


class MusicAssemblerSidParser(BaseSidParser):
    """:class:`pysidtracker.BaseSidParser` adapter for the shared API.

    ``parse``/``read`` yield the same :class:`~pymusicassembler.model.Song`
    as the module-level :func:`parse`/:func:`read`.  ``recognize`` is left as
    the inherited default (no reliable static player signature is asserted),
    so ``detect`` reports ``PlayroutineKind.UNKNOWN`` rather than a guess.
    """

    error_class = SidParseError

    def parse(self, data: bytes, **kwargs) -> Song:
        return parse(data)
