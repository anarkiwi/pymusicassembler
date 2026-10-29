"""Write a :class:`~pymusicassembler.model.Song` back to a Music Assembler image.

The Music Assembler player binary, its self-modified filter envelope and
its baked work-RAM seed are part of the loaded image; the song's musical
DATA (orderlists, patterns, instruments, the note-frequency tables and the
arpeggio programs) lives at per-tune base addresses discovered from the
player-code operands. The writer re-emits the structured DATA into those
regions of the image (via the same relocation-invariant signatures the
reader uses, so any load address round-trips, not just the standard $1021
layout), so a song read from an image round-trips byte-for-byte
(``write(read(image)) == image``) while a programmatically built or
edited song produces a valid, playable image.

``build`` returns the raw C64 image (the player + data, no load-address
prefix).  ``write`` wraps it as a PSID container or a bare ``.prg``, or
emits the editor's ``S.`` song (``container="native"``), which
:mod:`pymusicassembler.native` assembles in the editor's layout at any base.
"""

from pathlib import Path
from typing import Optional

from pysidtracker import SidError, SidImage, write_psid

from pymusicassembler import constants, grammar, native, reader
from pymusicassembler.errors import SidParseError, SongValidationError
from pymusicassembler.model import PresetBank, Song


def _put(image: SidImage, addr: int, values) -> None:
    """Write ``values`` into ``image`` at absolute address ``addr``.

    Writes past the current end grow the image; a write before the base is
    a corrupt pointer and raises :class:`SongValidationError`.
    """
    try:
        image.poke_bytes(addr, bytes(v & 0xFF for v in values))
    except SidError as exc:
        raise SongValidationError(str(exc)) from exc


def validate(song: Song) -> None:
    """Raise :class:`SongValidationError` if ``song`` cannot be emitted."""
    if not song.image:
        raise SongValidationError("song has no base image to re-emit into")
    if reader.image_in_io_area(song.load_addr, len(song.image)):
        raise SongValidationError(
            f"song image [{song.load_addr:#06x},{song.load_addr + len(song.image):#06x}) "
            f"reaches the C64 I/O area {constants.IO_AREA[0]:#06x}-"
            f"{constants.IO_AREA[1] - 1:#06x} (a C64 reads live I/O there, so no "
            "correct emit -- place the song in RAM, loaded below $D000)"
        )
    if len(song.orderlists) != constants.VOICES:
        raise SongValidationError(
            f"expected {constants.VOICES} orderlists, got {len(song.orderlists)}"
        )
    if len(song.inner_programs) != constants.ARP_COUNT:
        raise SongValidationError(
            f"expected {constants.ARP_COUNT} inner programs, got "
            f"{len(song.inner_programs)}"
        )
    for orderlist in song.orderlists:
        for entry in orderlist.entries:
            entry.control_byte()  # range-checks transpose/repeat


def _repack_inner_programs(
    image: SidImage, lay, load: int, image_len: int, inner_programs
) -> None:
    """Re-emit the stored arpeggio programs and their pointer-table entries.

    Each program keeps its original pointer address (any padding the file
    has between programs stays as found) and is only pushed past a program
    that grew before it; added programs go after the last one.
    """
    stored = [i for i, prog in enumerate(inner_programs) if prog is not None]
    if not stored:
        return
    lo, hi = lay.inner_lo, lay.inner_hi
    rule = grammar.program_rule(lay)
    end = load + image_len
    if not (load <= lo < end and load <= hi < end):
        raise SongValidationError("inner programs need a pointer table in the image")
    original = {i: image.ptr(lo, hi, i) for i in stored}
    anchored = sorted(
        (i for i in stored if original[i] and load <= original[i] < end),
        key=original.__getitem__,
    )
    # The file's reserved program region ends at the original pack end.
    pack_end = end
    if anchored:
        pack_end = max(
            original[i]
            + len(reader.parse_program(image, original[i], end, rule).to_bytes())
            for i in anchored
        )
    cursor = min((original[i] for i in anchored), default=image.end)
    for i in anchored:
        data = inner_programs[i].to_bytes()
        cursor = max(cursor, original[i])
        if cursor + len(data) > pack_end:
            raise SongValidationError(
                "arpeggio programs exceed the region the file reserves for them"
            )
        _put(image, cursor, data)
        _put(image, lo + i, (cursor & 0xFF,))
        _put(image, hi + i, ((cursor >> 8) & 0xFF,))
        cursor += len(data)
    # Programs the song adds beyond the file's go past the image end (data writes may have extended it: append after what is there).
    cursor = image.end
    for i in stored:
        if i in anchored:
            continue
        data = inner_programs[i].to_bytes()
        _put(image, cursor, data)
        _put(image, lo + i, (cursor & 0xFF,))
        _put(image, hi + i, ((cursor >> 8) & 0xFF,))
        cursor += len(data)
    # Empty slots must read back empty: clear a pointer the file claims as a program; leave placeholder pointers (non-program targets) as-is.
    for i in range(constants.ARP_COUNT):
        if inner_programs[i] is not None:
            continue
        ptr = image.ptr(lo, hi, i)
        if not (ptr and load <= ptr < end):
            continue
        try:
            reader.parse_program(image, ptr, end, rule)
        except SidParseError:
            continue
        _put(image, lo + i, (0,))
        _put(image, hi + i, (0,))


def build(song: Song) -> bytes:
    """Serialize ``song`` to a raw Music Assembler C64 image."""
    if song.layout_post_init:
        raise SongValidationError(
            "tune's tables were located only by running its init (packed or "
            "relocating rip), so it is read-only and cannot be written back"
        )
    validate(song)
    load = song.load_addr
    image = reader.load_view(song.image, load)
    try:
        lay = reader.discover_layout(image, load, load, load + len(song.image))
    except SidParseError as exc:
        raise SongValidationError(str(exc)) from exc
    grammar.check(song, lay)
    bases = {name: getattr(lay, name) for name in reader.TABLES}

    # Orderlists: re-emit each voice's entries and exact terminator at its pointer address.
    for voice, orderlist in enumerate(song.orderlists):
        addr = image.ptr(bases["order_lo"], bases["order_hi"], voice)
        _put(image, addr, orderlist.to_bytes())

    # Patterns: write each pattern's raw bytes + terminator at its address.
    for pattern_id, pattern in enumerate(song.patterns):
        if pattern is None:  # an id the player never reads
            continue
        addr = image.ptr(bases["pattern_lo"], bases["pattern_hi"], pattern_id)
        _put(image, addr, pattern.to_bytes())

    # Instruments: 8-byte records in the instrument table.
    for iid, instrument in enumerate(song.instruments):
        _put(
            image,
            bases["instr"] + iid * constants.INSTR_RECORD_SIZE,
            instrument.to_bytes(),
        )

    # Note-frequency tables.
    if song.freq_lo:
        _put(image, bases["freq_lo"], song.freq_lo)
    if song.freq_hi:
        _put(image, bases["freq_hi"], song.freq_hi)

    # Arpeggio (inner) programs + their pointer-table entries.
    _repack_inner_programs(image, lay, load, len(song.image), song.inner_programs)

    return _trim_zero_fill(image.to_prg()[2:], len(song.image))


def _trim_zero_fill(image: bytes, source_len: int) -> bytes:
    """``image`` without the zero bytes it gained past the source's end.

    The reader zero-fills a record the source truncates (the editor saves
    its preset table up to the last non-zero byte), so those zeros are not
    re-emitted and ``build(read(image)) == image``.
    """
    return image[: max(source_len, len(image.rstrip(b"\0")))]


def build_native(song: Song, base: Optional[int] = None) -> bytes:
    """Serialize ``song`` to the native editor song body, relocated to ``base``.

    No load-address prefix: :func:`write` adds it for ``container="native"``.
    ``base`` defaults to the source's player base (see :mod:`~pymusicassembler.native`).
    """
    validate(song)
    return native.assemble(song, base)[1]


def write(song: Song, dst, container: str = "auto", base: Optional[int] = None) -> Path:
    """Write ``song`` to ``dst``.

    ``container`` is ``"auto"`` (default: re-emit the original container
    header read from the source so a ``.sid`` round-trips byte-identically,
    else synthesize a PSID), ``"psid"`` (always synthesize a PSID header),
    ``"prg"`` (a bare load-address-prefixed image), or ``"native"`` (the
    Music Assembler editor ``S.`` song: a self-starting ``.prg`` the Triad
    editor loads, relocated to ``base``; default the source's player base).
    """
    if container == "native":
        validate(song)
        base, body = native.assemble(song, base)
        blob = bytes((base & 0xFF, base >> 8)) + body
        Path(dst).write_bytes(blob)
        return Path(dst)
    image = build(song)
    if container == "auto" and song.container_header:
        blob = song.container_header + image
    elif container == "prg":
        blob = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + image
    elif container in ("psid", "auto"):
        blob = _wrap_psid(song, image)
    else:
        raise SongValidationError(f"unknown container {container!r}")
    Path(dst).write_bytes(blob)
    return Path(dst)


def _wrap_psid(song: Song, image: bytes) -> bytes:
    """Wrap a raw image in a minimal PSID v2 container."""
    return write_psid(
        load=song.load_addr,
        init=song.init_addr,
        play=song.play_addr,
        image=image,
        name=song.name,
        author=song.author,
        released=song.released,
    )


def build_presets(bank: PresetBank) -> bytes:
    """Serialize a preset bank to its 768-byte body (no load prefix)."""
    return bank.to_bytes()


def write_presets(bank: PresetBank, dst) -> Path:
    """Write ``bank`` as the editor's ``P.`` file: load ``$4300`` + body."""
    body = build_presets(bank)
    blob = (
        bytes((constants.PRESET_BANK_LOAD & 0xFF, constants.PRESET_BANK_LOAD >> 8))
        + body
    )
    Path(dst).write_bytes(blob)
    return Path(dst)
