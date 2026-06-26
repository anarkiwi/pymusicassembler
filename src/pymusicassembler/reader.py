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

import struct
from pathlib import Path
from typing import List, Tuple

from pymusicassembler import constants
from pymusicassembler.errors import SidParseError
from pymusicassembler.model import (
    Instrument,
    OrderEntry,
    Orderlist,
    Pattern,
    Song,
)

_PSID_HEADER = struct.Struct(">4sHHHHHHHI")  # magic..speed


def _read_cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _parse_container(
    data: bytes,
) -> Tuple[int, int, int, str, str, str, bytes, bytes]:
    """Return (load, init, play, name, author, released, image, header).

    ``header`` is the exact container bytes that precede ``image`` (the
    PSID/RSID header + any embedded load-address prefix), kept so the
    writer can re-emit a byte-identical container.
    """
    magic = data[:4]
    if magic in (b"PSID", b"RSID"):
        if len(data) < _PSID_HEADER.size:
            raise SidParseError("truncated PSID/RSID header")
        _m, _ver, data_off, load, init, play, _songs, _start, _speed = (
            _PSID_HEADER.unpack_from(data, 0)
        )
        name = _read_cstr(data[22:54])
        author = _read_cstr(data[54:86])
        released = _read_cstr(data[86:118])
        body = data[data_off:]
        if load == 0:  # load address is the first 2 bytes of the body
            if len(body) < 2:
                raise SidParseError("truncated PSID body")
            load = body[0] | (body[1] << 8)
            header = data[: data_off + 2]
            image = body[2:]
        else:
            header = data[:data_off]
            image = body
        if init == 0:
            init = load
        if play == 0:
            play = load
        return load, init, play, name, author, released, image, header
    # Bare .prg: 2-byte little-endian load address + image.
    if len(data) < 2:
        raise SidParseError("truncated .prg image")
    load = data[0] | (data[1] << 8)
    return (
        load,
        constants.DEFAULT_INIT,
        constants.DEFAULT_PLAY,
        "",
        "",
        "",
        data[2:],
        data[:2],
    )


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


class _Image:
    """Absolute-addressed read view of a loaded image."""

    def __init__(self, image: bytes, load: int):
        self.image = image
        self.load = load

    def byte(self, addr: int) -> int:
        idx = addr - self.load
        if 0 <= idx < len(self.image):
            return self.image[idx]
        return 0

    def ptr(self, lo_base: int, hi_base: int, index: int) -> int:
        return self.byte(lo_base + index) | (self.byte(hi_base + index) << 8)


def _parse_orderlist(img: _Image, bases: dict, voice: int) -> Orderlist:
    addr = img.ptr(bases["order_lo"], bases["order_hi"], voice)
    entries: List[OrderEntry] = []
    cur = 0
    seen = set()
    while True:
        if cur in seen:  # defensive: never loop forever on a malformed list
            break
        seen.add(cur)
        pid = img.byte(addr + cur)
        if pid == constants.ORDER_END:
            break
        ctl = img.byte(addr + cur + 1)
        entries.append(OrderEntry.from_bytes(pid, ctl))
        cur += 2
    return Orderlist(entries=entries)


def _parse_pattern(img: _Image, bases: dict, pattern_id: int) -> Pattern:
    """Extract a pattern's raw bytes, walking the grammar so an inline
    filter/vibrato argument byte that happens to be ``$FF`` is not mistaken
    for the pattern terminator (the terminator is only an ``$FF`` read at a
    row-START position, matching the player's $1187 end check)."""
    addr = img.ptr(bases["pattern_lo"], bases["pattern_hi"], pattern_id)
    data: List[int] = []
    cur = 0
    limit = 0x1000  # defensive bound
    while cur < limit:
        val = img.byte(addr + cur)
        if val == constants.PATTERN_END:  # row-start terminator
            break
        if val >= constants.INSTR_MIN:
            if val >= constants.LONGDUR_MIN:  # long duration: single byte
                data.append(val)
                cur += 1
                continue
            data.append(val)  # instrument select; may continue into a note
            cur += 1
            nxt = img.byte(addr + cur)
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
        ctl = img.byte(addr + cur)
        data.append(ctl)  # control byte
        cur += 1
        if ctl >= constants.CTL_FILTER:  # filter command: 2 inline arg bytes
            data.extend((img.byte(addr + cur), img.byte(addr + cur + 1)))
            cur += 2
        elif ctl & constants.CTL_VIBARP:  # vibrato/arp params: 2 inline bytes
            data.extend((img.byte(addr + cur), img.byte(addr + cur + 1)))
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
    img = _Image(image, load)

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
                img.byte(bases["instr"] + iid * constants.INSTR_RECORD_SIZE + k)
                for k in range(constants.INSTR_RECORD_SIZE)
            )
        )
        for iid in range(max_iid + 1)
    ]

    freq_lo = [img.byte(bases["freq_lo"] + n) for n in range(constants.FREQ_TABLE_LEN)]
    freq_hi = [img.byte(bases["freq_hi"] + n) for n in range(constants.FREQ_TABLE_LEN)]

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
