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

from pysidtracker import SidError, SidImage, load_prefix, require_ram, write_psid
from pysidtracker.errors import reraise_as

from pymusicassembler import constants, grammar, native, reader
from pymusicassembler.errors import MusicAssemblerParseError, SongValidationError
from pymusicassembler.model import PresetBank, Song


def _put(image: SidImage, addr: int, values) -> None:
    """Write ``values`` into ``image`` at absolute address ``addr``.

    Writes past the current end grow the image; a write before the base is
    a corrupt pointer and raises :class:`SongValidationError`.
    """
    with reraise_as(SongValidationError, SidError):
        image.poke_bytes(addr, bytes(v & 0xFF for v in values))


def _put_ptr(image: SidImage, lo: int, hi: int, index: int, addr: int) -> None:
    """:meth:`SidImage.put_ptr`, raising :class:`SongValidationError` (see :func:`_put`)."""
    with reraise_as(SongValidationError, SidError):
        image.put_ptr(lo, hi, index, addr)


def validate(song: Song) -> None:
    """Raise :class:`SongValidationError` if ``song`` cannot be emitted."""
    if not song.image:
        raise SongValidationError("song has no base image to re-emit into")
    require_ram(song.load_addr, len(song.image), "song image", SongValidationError)
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


def _repack_inner_programs(image: SidImage, lay, region: range, inner_programs) -> None:
    """Re-emit the stored arpeggio programs and their pointer-table entries.

    Each program keeps its original pointer address (any padding the file
    has between programs stays as found) and is only pushed past a program
    that grew before it; added programs go after the last one.
    """
    stored = [i for i, prog in enumerate(inner_programs) if prog is not None]
    if not stored:
        return
    lo, hi = lay.inner_lo, lay.inner_hi
    if not (lo in region and hi in region):
        raise SongValidationError("inner programs need a pointer table in the image")
    source = [
        reader.stored_program(image, lay, i, region) for i in range(constants.ARP_COUNT)
    ]
    original = {i: image.ptr(lo, hi, i) for i in stored}
    anchored = sorted(
        (i for i in stored if original[i] and original[i] in region),
        key=original.__getitem__,
    )
    # The file's reserved program region ends at the original pack end.
    pack_end = max(
        (
            original[i] + len(source[i].to_bytes() if source[i] else b"")
            for i in anchored
        ),
        default=region.stop,
    )
    cursor = min((original[i] for i in anchored), default=image.end)
    for i in anchored:
        cursor = max(cursor, original[i])
        if cursor + len(inner_programs[i].to_bytes()) > pack_end:
            raise SongValidationError(
                "arpeggio programs exceed the region the file reserves for them"
            )
        cursor = _put_program(image, lay, i, cursor, inner_programs[i])
    # Programs the song adds beyond the file's go past what the image now holds.
    cursor = image.end
    for i in stored:
        if i not in anchored:
            cursor = _put_program(image, lay, i, cursor, inner_programs[i])
    # A slot the song emptied must read back empty; placeholders stay as found.
    for i, program in enumerate(inner_programs):
        if program is None and source[i] is not None:
            _put_ptr(image, lo, hi, i, 0)


def _put_program(image: SidImage, lay, slot: int, addr: int, program) -> int:
    """Store ``program`` at ``addr`` as ``slot``'s; the address after it."""
    data = program.to_bytes()
    _put(image, addr, data)
    _put_ptr(image, lay.inner_lo, lay.inner_hi, slot, addr)
    return addr + len(data)


def build(song: Song) -> bytes:
    """Serialize ``song`` to a raw Music Assembler C64 image."""
    if song.layout_post_init:
        raise SongValidationError(
            "tune's tables were located only by running its init (packed or "
            "relocating rip), so it is read-only and cannot be written back"
        )
    validate(song)
    load = song.load_addr
    region = range(load, load + len(song.image))
    image = reader.load_view(song.image, load)
    with reraise_as(SongValidationError, MusicAssemblerParseError):
        lay = reader.discover_layout(image, load, region.start, region.stop)
    grammar.check(song, lay)

    for voice, orderlist in enumerate(song.orderlists):
        _put(image, image.ptr(lay.order_lo, lay.order_hi, voice), orderlist.to_bytes())
    for pattern_id, pattern in enumerate(song.patterns):
        if pattern is not None:  # None: an id the player never reads
            addr = image.ptr(lay.pattern_lo, lay.pattern_hi, pattern_id)
            _put(image, addr, pattern.to_bytes())
    _put(image, lay.instr, b"".join(i.to_bytes() for i in song.instruments))
    _put(image, lay.freq_lo, song.freq_lo)
    _put(image, lay.freq_hi, song.freq_hi)
    _repack_inner_programs(image, lay, region, song.inner_programs)

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
        blob = load_prefix(base) + body
        Path(dst).write_bytes(blob)
        return Path(dst)
    image = build(song)
    if container == "auto" and song.container_header:
        blob = song.container_header + image
    elif container == "prg":
        blob = load_prefix(song.load_addr) + image
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
    Path(dst).write_bytes(load_prefix(constants.PRESET_BANK_LOAD) + build_presets(bank))
    return Path(dst)
