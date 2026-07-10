"""Write a :class:`~pymusicassembler.model.Song` back to a Music Assembler image.

The Music Assembler player binary, its self-modified filter envelope and
its baked work-RAM seed are part of the loaded image; the song's musical
DATA (orderlists, patterns, instruments and the note-frequency tables)
lives at per-tune base addresses discovered from the player-code
operands.  The writer re-emits the structured DATA into those regions of
the image (via the same relocation-invariant signatures the reader uses,
so any load address round-trips, not just the standard $1021 layout), so a
song read from an image round-trips byte-for-byte
(``write(read(image)) == image``) while a programmatically built or
edited song produces a valid, playable image.

``build`` returns the raw C64 image (the player + data, no load-address
prefix).  ``write`` wraps it as a PSID container, a bare ``.prg``, or the
native Music Assembler editor song format (``container="native"``): the
player+data image at its base with a self-starting IRQ-install stub in
place of the PSID header -- the ``S.`` files the Triad editor loads.
"""

import struct
from pathlib import Path
from typing import Tuple

from pysidtracker import SidError, SidImage, write_psid

from pymusicassembler import constants, reader
from pymusicassembler.errors import SidParseError, SongValidationError
from pymusicassembler.model import Song


def _put(image: SidImage, addr: int, values) -> None:
    """Write ``values`` into ``image`` at absolute address ``addr``.

    Writes past the current end grow the image (a table read past the
    source's end -- e.g. an instrument whose record the source truncated --
    is zero-filled by the reader and re-emitted in full); a write before
    the base is a corrupt pointer and raises :class:`SongValidationError`.
    """
    try:
        image.poke_bytes(addr, bytes(v & 0xFF for v in values))
    except SidError as exc:
        raise SongValidationError(str(exc)) from exc


def validate(song: Song) -> None:
    """Raise :class:`SongValidationError` if ``song`` cannot be emitted."""
    if not song.image:
        raise SongValidationError("song has no base image to re-emit into")
    if len(song.orderlists) != constants.VOICES:
        raise SongValidationError(
            f"expected {constants.VOICES} orderlists, got {len(song.orderlists)}"
        )
    for orderlist in song.orderlists:
        for entry in orderlist.entries:
            entry.control_byte()  # range-checks transpose/repeat


def build(song: Song) -> bytes:
    """Serialize ``song`` to a raw Music Assembler C64 image."""
    validate(song)
    load = song.load_addr
    image = reader.load_view(song.image, load)
    try:
        bases = reader.discover_bases(image, load, len(song.image))
    except SidParseError as exc:
        raise SongValidationError(str(exc)) from exc

    # Orderlists: write each voice's entries followed by the $FF terminator,
    # in place at the address its pointer-table entry references.
    for voice, orderlist in enumerate(song.orderlists):
        addr = image.ptr(bases["order_lo"], bases["order_hi"], voice)
        flat = []
        for entry in orderlist.entries:
            flat.extend((entry.pattern_id & 0xFF, entry.control_byte()))
        flat.append(constants.ORDER_END)
        _put(image, addr, flat)

    # Patterns: write each pattern's raw bytes + terminator at its address.
    for pattern_id, pattern in enumerate(song.patterns):
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

    return image.to_prg()[2:]


# The 33-byte Music Assembler self-start / IRQ-install stub, as base $0000
# (init=$0048, IRQ vector=$0018, play=$0021).  Disassembly:
#   SEI; JSR init; LDA #<irq; LDY #>irq; STA $0314; STY $0315   (IRQ vector)
#   INX; STX $DC0E; INX; STX $D01A; CLI; RTS                    (start timer)
#   INC $D019; JSR play; JMP $EA31                              (IRQ handler)
# Only the three embedded addresses relocate with the base.
_NATIVE_STUB = bytes.fromhex(
    "78204800a918a0008d14038c1503e88e0edce88e1ad05860ee19d02021004c31ea"
)


def _native_stub(base: int) -> bytes:
    """The self-start stub relocated to ``base`` (see :data:`_NATIVE_STUB`)."""
    stub = bytearray(_NATIVE_STUB)
    stub[2:4] = struct.pack("<H", base + constants.NATIVE_INIT_OFFSET)
    irq = base + constants.NATIVE_IRQ_OFFSET
    stub[5], stub[7] = irq & 0xFF, irq >> 8
    stub[28:30] = struct.pack("<H", base + constants.NATIVE_PLAY_OFFSET)
    return bytes(stub)


def _native(song: Song) -> Tuple[int, bytes]:
    """Return ``(base, body)`` for the native self-starting song image.

    ``base`` is the load address the native ``.prg`` takes; ``body`` is the
    self-start stub followed by the player+data from the play routine
    onward.  Raises :class:`SongValidationError` when the player is not at a
    standard Music Assembler layout (so the stub's fixed entry offsets would
    not line up).
    """
    image = build(song)
    view = reader.load_view(song.image, song.load_addr)
    base = reader.find_player_base(view, song.load_addr, len(song.image))
    if base is None:
        raise SongValidationError(
            "player is not at a standard Music Assembler layout; "
            "cannot emit the native editor format"
        )
    tail = image[base + constants.NATIVE_PLAY_OFFSET - song.load_addr :]
    return base, _native_stub(base) + tail


def build_native(song: Song) -> bytes:
    """Serialize ``song`` to a native Music Assembler editor song image.

    The body (no load-address prefix) is the self-start stub + player +
    data; it loads at the base :func:`_native` reports, which
    :func:`write` prefixes for the ``"native"`` container.
    """
    return _native(song)[1]


def write(song: Song, dst, container: str = "auto") -> Path:
    """Write ``song`` to ``dst``.

    ``container`` is ``"auto"`` (default: re-emit the original container
    header read from the source so a ``.sid`` round-trips byte-identically,
    else synthesize a PSID), ``"psid"`` (always synthesize a PSID header),
    ``"prg"`` (a bare load-address-prefixed image), or ``"native"`` (the
    Music Assembler editor ``S.`` song: a self-starting ``.prg`` the Triad
    editor loads).
    """
    if container == "native":
        base, body = _native(song)
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
