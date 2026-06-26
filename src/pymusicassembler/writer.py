"""Write a :class:`~pymusicassembler.model.Song` back to a Music Assembler image.

The Music Assembler player binary, its self-modified filter envelope and
its baked work-RAM seed are part of the loaded image; the song's musical
DATA (orderlists, patterns, instruments and the note-frequency tables)
lives at per-tune base addresses discovered from the player-code
operands.  The writer re-emits the structured DATA into those regions of
the image, so a song read from an image round-trips byte-for-byte
(``write(read(image)) == image``) while a programmatically built or
edited song produces a valid, playable image.

``build`` returns the raw C64 image (the player + data, no load-address
prefix); ``write`` wraps it as a PSID container or a bare ``.prg``.
"""

import struct
from pathlib import Path
from typing import List

from pymusicassembler import constants
from pymusicassembler.errors import SongValidationError
from pymusicassembler.model import Song

_PSID_HEADER = struct.Struct(">4sHHHHHHHI")


def _op16(image: List[int], off: int) -> int:
    return image[off] | (image[off + 1] << 8)


def _put(image: List[int], addr: int, load: int, values) -> None:
    """Write ``values`` into the image at absolute address ``addr``."""
    start = addr - load
    for i, value in enumerate(values):
        idx = start + i
        if not 0 <= idx < len(image):
            raise SongValidationError(f"write at ${addr + i:04X} is outside the image")
        image[idx] = value & 0xFF


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
    image = list(song.image)
    load = song.load_addr
    bases = {
        "order_lo": _op16(image, constants.OP_ORDER_LO),
        "order_hi": _op16(image, constants.OP_ORDER_HI),
        "pattern_lo": _op16(image, constants.OP_PATTERN_LO),
        "pattern_hi": _op16(image, constants.OP_PATTERN_HI),
        "instr": _op16(image, constants.OP_INSTR),
        "freq_lo": _op16(image, constants.OP_FREQ_LO),
        "freq_hi": _op16(image, constants.OP_FREQ_HI),
    }

    # Orderlists: write each voice's entries followed by the $FF terminator,
    # in place at the address its pointer-table entry references.
    for voice, orderlist in enumerate(song.orderlists):
        addr = image[bases["order_lo"] + voice - load] | (
            image[bases["order_hi"] + voice - load] << 8
        )
        flat: List[int] = []
        for entry in orderlist.entries:
            flat.extend((entry.pattern_id & 0xFF, entry.control_byte()))
        flat.append(constants.ORDER_END)
        _put(image, addr, load, flat)

    # Patterns: write each pattern's raw bytes + terminator at its address.
    for pattern_id, pattern in enumerate(song.patterns):
        addr = image[bases["pattern_lo"] + pattern_id - load] | (
            image[bases["pattern_hi"] + pattern_id - load] << 8
        )
        _put(image, addr, load, pattern.to_bytes())

    # Instruments: 8-byte records in the instrument table.
    for iid, instrument in enumerate(song.instruments):
        _put(
            image,
            bases["instr"] + iid * constants.INSTR_RECORD_SIZE,
            load,
            instrument.to_bytes(),
        )

    # Note-frequency tables.
    if song.freq_lo:
        _put(image, bases["freq_lo"], load, song.freq_lo)
    if song.freq_hi:
        _put(image, bases["freq_hi"], load, song.freq_hi)

    return bytes(image)


def write(song: Song, dst, container: str = "auto") -> Path:
    """Write ``song`` to ``dst``.

    ``container`` is ``"auto"`` (default: re-emit the original container
    header read from the source so a ``.sid`` round-trips byte-identically,
    else synthesize a PSID), ``"psid"`` (always synthesize a PSID header),
    or ``"prg"`` (a bare load-address-prefixed image).
    """
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


def _pad(text: str) -> bytes:
    return text.encode("latin-1")[:31].ljust(32, b"\0")


def _wrap_psid(song: Song, image: bytes) -> bytes:
    """Wrap a raw image in a minimal PSID v2 container."""
    data_off = 0x7C
    header = _PSID_HEADER.pack(
        b"PSID",
        2,  # version
        data_off,
        song.load_addr,
        song.init_addr,
        song.play_addr,
        1,  # songs
        1,  # start song
        0,  # speed
    )
    # PSID v2 adds name/author/released + the v2 trailer (flags, startPage,
    # pageLength, secondSIDAddress, thirdSIDAddress = 6 bytes), filling the
    # header to data_off ($7C) exactly.
    body = header + _pad(song.name) + _pad(song.author) + _pad(song.released)
    body += struct.pack(">HBBBB", 0, 0, 0, 0, 0)
    body = body.ljust(data_off, b"\0")
    return body + image
