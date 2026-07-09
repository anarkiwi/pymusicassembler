"""Read a Music Assembler tune (PSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The Music Assembler
player binary is relocated per tune (across HVSC the same player loads at
$1000, $c000, ... -- only a handful load at the $1021 the original fixed
operand offsets were calibrated to), so a pointer-table base cannot be
read at a fixed image offset.  The reader instead locates each
table-loading instruction by its opcode skeleton (a relocation-invariant
signature) and reads the base from its operand, then follows the bases to
extract the orderlists, patterns, instruments and note-frequency tables.
An unsupported player variant (no signature match, or a base outside the
loaded image) raises :class:`SidParseError` rather than yielding garbage.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

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


def _w16(operand: bytes) -> int:
    return operand[0] | (operand[1] << 8)


# Player-code instruction signatures for each table-base operand.  The
# Music Assembler player is relocated per tune (HVSC tunes load at $1000,
# $c000, ... -- only 75 of ~6500 load at the $1021 the fixed operand
# offsets were calibrated to), so a base cannot be read at a fixed offset.
# Each base is instead read from the operand of the ``LDA table,X/Y`` that
# consumes it, located by the instruction's opcode skeleton (operand bytes
# wildcarded).  The store targets that anchor them are relocation-invariant:
# the orderlist/pattern pointers land in zero page ($fa/$fb) which never
# moves, and the frequency work registers keep their +3 (per-voice) stride.
_SIG_ORDER = re.compile(rb"\xbd(..)\x85\xfa\xbd(..)\x85\xfb", re.S)  # LDA a,X;STA $fa
_SIG_PATTERN = re.compile(rb"\xb9(..)\x85\xfa\xb9(..)\x85\xfb", re.S)  # LDA a,Y;STA $fa
_SIG_INSTR = re.compile(
    rb"\x9d(..)\xb9(..)\x85\xfa", re.S
)  # STA a,X;LDA instr,Y;STA $fa
_SIG_FREQ = re.compile(rb"\xb9(..)\x9d(..)\xb9(..)\x9d(..)", re.S)  # two LDA,Y;STA,X


def _find_freq(image: bytes) -> Optional[re.Match]:
    """First ``LDA freq_lo,Y; STA work; LDA freq_hi,Y; STA work+3`` pair.

    The two per-voice frequency work registers are three bytes apart in
    every build, so the +3 store stride identifies the frequency loads
    without depending on their (relocated) absolute work-RAM address.
    """
    for match in _SIG_FREQ.finditer(image):
        if _w16(match.group(4)) == _w16(match.group(2)) + 3:
            return match
    return None


def _match_signatures(image: bytes) -> dict:
    """Locate the four table-load instruction signatures in ``image``.

    Returns a dict of the (still-present-or-``None``) match objects, so
    both :func:`_discover_bases` and the parser's ``recognize`` predicate
    share one signature scan.
    """
    return {
        "order": _SIG_ORDER.search(image),
        "pattern": _SIG_PATTERN.search(image),
        "instr": _SIG_INSTR.search(image),
        "freq": _find_freq(image),
    }


def _discover_bases(image: bytes, load: int) -> dict:
    """Discover per-tune table base addresses from player-code operands.

    Raises :class:`SidParseError` when the player-code signatures are
    absent (an unsupported Music Assembler variant) or when a discovered
    base falls outside the loaded image (a spurious match).
    """
    sig = _match_signatures(image)
    order, pattern, instr, freq = (
        sig["order"],
        sig["pattern"],
        sig["instr"],
        sig["freq"],
    )
    if not all((order, pattern, instr, freq)):
        missing = ",".join(name for name, match in sig.items() if match is None)
        raise SidParseError(
            f"player signatures not found ({missing}); unsupported variant"
        )
    bases = {
        "order_lo": _w16(order.group(1)),
        "order_hi": _w16(order.group(2)),
        "pattern_lo": _w16(pattern.group(1)),
        "pattern_hi": _w16(pattern.group(2)),
        "instr": _w16(instr.group(2)),
        "freq_lo": _w16(freq.group(1)),
        "freq_hi": _w16(freq.group(3)),
    }
    end = load + len(image)
    for name, base in bases.items():
        if not load <= base < end:
            raise SidParseError(
                f"discovered {name} base {base:#06x} outside image "
                f"[{load:#06x},{end:#06x}); unsupported variant"
            )
    return bases


def _load_view(image: bytes, load: int) -> SidImage:
    """Absolute-addressed read view of ``image`` placed at ``load``.

    Uses :class:`pysidtracker.SidImage`; ``peek`` returns 0 out of range,
    matching the reader's reliance on a zero fallback.
    """
    return SidImage.from_prg(bytes((load & 0xFF, load >> 8)) + image)


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
    raise SidParseError(
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
    bases = _discover_bases(image, load)
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
    as the module-level :func:`parse`/:func:`read`.  ``recognize`` reports the
    player statically: it returns the order-signature offset when all four
    table-load instruction signatures are present in the loaded image, so
    ``detect`` classifies a direct-load Music Assembler tune as
    ``PlayroutineKind.DIRECT`` (and packed/relocating variants once init runs).
    """

    error_class = SidParseError

    def parse(self, data: bytes, **kwargs) -> Song:
        return parse(data)

    def recognize(self, image: SidImage) -> object:
        """Return the order-signature offset if the MA player is present.

        The four table-load instruction signatures (order/pattern/instr/freq
        stores) together are specific to the Music Assembler player; requiring
        all four avoids false positives.  Searches the currently loaded image
        region, so it also recognises the player after ``detect`` runs init
        for a packed/relocating tune.
        """
        region = bytes(image.mem[image.load : image.end])
        sig = _match_signatures(region)
        if all(sig.values()):
            return image.load + sig["order"].start()
        return None
