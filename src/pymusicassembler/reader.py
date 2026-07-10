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

from typing import Dict, List, Optional, Tuple

from pysidtracker import (
    BaseSidParser,
    CodePattern,
    Match,
    SidError,
    SidImage,
    find_code_all,
    find_code_first,
    read_bytes,
)

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


# Player-code instruction signatures for each table-base operand.  The
# Music Assembler player is relocated per tune (HVSC tunes load at $1000,
# $c000, ... -- only 75 of ~6500 load at the $1021 the fixed operand
# offsets were calibrated to), so a base cannot be read at a fixed offset.
# Each base is instead read from the operand of the ``LDA table,X/Y`` that
# consumes it, located by the instruction's opcode skeleton (operand words
# captured, wildcarded).  The store targets that anchor them are
# relocation-invariant: the orderlist/pattern pointers land in zero page
# ($fa/$fb) which never moves, and the frequency work registers keep their
# +3 (per-voice) stride.
_PAT_ORDER = CodePattern(
    "BD {order_lo:w} 85 FA BD {order_hi:w} 85 FB"
)  # LDA a,X;STA $fa
_PAT_PATTERN = CodePattern(
    "B9 {pattern_lo:w} 85 FA B9 {pattern_hi:w} 85 FB"
)  # LDA a,Y;STA $fa
_PAT_INSTR = CodePattern("9D ?? ?? B9 {instr:w} 85 FA")  # STA a,X;LDA instr,Y;STA $fa
_PAT_FREQ = CodePattern(
    "B9 {freq_lo:w} 9D {work_lo:w} B9 {freq_hi:w} 9D {work_hi:w}"
)  # two LDA,Y;STA,X
# Play-routine entry: ``LDX #0; DEC tempo; BMI +$0c; JSR``.  The tempo
# prescaler cell is base+$90, so ``base = tempo - $90`` locates the player
# base independent of relocation (used to emit the native self-starting
# format at ``base``, where the play routine sits at base+$21).
_PAT_PLAY = CodePattern("A2 00 CE {tempo:w} 30 0C 20 26")


def _find_freq(image: SidImage, start: int, end: int) -> Optional[Match]:
    """First ``LDA freq_lo,Y; STA work; LDA freq_hi,Y; STA work+3`` pair.

    The two per-voice frequency work registers are three bytes apart in
    every build, so the +3 store stride identifies the frequency loads
    without depending on their (relocated) absolute work-RAM address.
    """
    for match in find_code_all(image, _PAT_FREQ, start=start, end=end):
        if match.captures["work_hi"] == match.captures["work_lo"] + 3:
            return match
    return None


def _match_signatures(
    image: SidImage, start: int, end: int
) -> Dict[str, Optional[Match]]:
    """Locate the four table-load instruction signatures in ``image``.

    Returns a dict of the (still-present-or-``None``) matches over
    ``[start, end)``, so both :func:`discover_bases` and the parser's
    ``recognize`` predicate share one signature scan.
    """
    return {
        "order": find_code_first(image, _PAT_ORDER, start=start, end=end),
        "pattern": find_code_first(image, _PAT_PATTERN, start=start, end=end),
        "instr": find_code_first(image, _PAT_INSTR, start=start, end=end),
        "freq": _find_freq(image, start, end),
    }


def discover_bases(image: SidImage, load: int, image_len: int) -> dict:
    """Discover per-tune table base addresses from player-code operands.

    Raises :class:`SidParseError` when the player-code signatures are
    absent (an unsupported Music Assembler variant) or when a discovered
    base falls outside the loaded image (a spurious match).
    """
    end = load + image_len
    sig = _match_signatures(image, load, end)
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
        "order_lo": order.captures["order_lo"],
        "order_hi": order.captures["order_hi"],
        "pattern_lo": pattern.captures["pattern_lo"],
        "pattern_hi": pattern.captures["pattern_hi"],
        "instr": instr.captures["instr"],
        "freq_lo": freq.captures["freq_lo"],
        "freq_hi": freq.captures["freq_hi"],
    }
    for name, base in bases.items():
        if not load <= base < end:
            raise SidParseError(
                f"discovered {name} base {base:#06x} outside image "
                f"[{load:#06x},{end:#06x}); unsupported variant"
            )
    return bases


def load_view(image: bytes, load: int) -> SidImage:
    """Absolute-addressed read view of ``image`` placed at ``load``.

    Uses :class:`pysidtracker.SidImage`; ``peek`` returns 0 out of range,
    matching the reader's reliance on a zero fallback.
    """
    return SidImage.from_prg(bytes((load & 0xFF, load >> 8)) + image)


def find_player_base(image: SidImage, load: int, image_len: int) -> Optional[int]:
    """Locate the player base address, or ``None`` if the layout is
    non-standard.

    The play routine's tempo-prescaler cell is ``base + 0x90``, found from
    the ``LDX #0; DEC tempo`` play entry (:data:`_PAT_PLAY`).  A genuine
    Music Assembler image loads either at ``base`` (the self-start stub /
    PSID vectors precede the play routine) or at ``base + 0x21`` (a rip
    that begins at the play routine itself); any other offset means the
    player is not at the standard base+$21 entry, so the base is rejected.
    """
    end = load + image_len
    for match in find_code_all(image, _PAT_PLAY, start=load, end=end):
        base = match.captures["tempo"] - constants.NATIVE_TEMPO_OFFSET
        if load - base in (0, constants.NATIVE_PLAY_OFFSET):
            return base
    return None


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
    img = load_view(image, load)
    bases = discover_bases(img, load, len(image))

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
        sig = _match_signatures(image, image.load, image.end)
        if all(sig.values()):
            return sig["order"].addr
        return None
