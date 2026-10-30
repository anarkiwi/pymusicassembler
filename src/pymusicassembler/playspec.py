"""The Music Assembler playroutine as a ``pysidtracker.playspec`` spec.

``specs/musicassembler.play`` states the play call; :func:`binding` resolves its
``layout`` from :mod:`pymusicassembler.layout` discovery plus the three cells the
play code fixes in zero page and its self-modified pitch operand.
"""

import dataclasses
from importlib import resources

from pysidtracker import CodePattern, SidImage, find_code_all
from pysidtracker.playspec import parse as parse_spec

from pymusicassembler import layout

SPEC = parse_spec(
    resources.files(__package__).joinpath("specs/musicassembler.play").read_text()
)

ZP_PTR = CodePattern("B9 {table:w} 85 {zp} B9 ?? ?? 85")
ZP_TMP = CodePattern("85 {zp} C8 BD {transrep:w} 4A 4A 4A 4A")
PITCH_OP = CodePattern("29 7F 8D {op:w} C8 B1 ?? F0 03 8D {acc:w}")
ADVANCE = CodePattern("DE {dur:w} 30 {offset}")


def _image(view) -> SidImage:
    return SidImage.from_prg(b"\0\0" + bytes(view))


def _zp(image, pattern, key, value):
    return next(
        m.captures["zp"]
        for m in find_code_all(image, pattern)
        if m.captures[key] == value
    )


def binding(view, entry):
    """The playspec layout of the Music Assembler instance at ``entry`` in the
    64 KiB ``view``."""
    found = dataclasses.asdict(layout.discover(view, entry))
    found.pop("variant")
    image = _image(view)
    found["zp_ptr"] = _zp(image, ZP_PTR, "table", found["pattern_lo"])
    found["zp_tmp"] = _zp(image, ZP_TMP, "transrep", found["transrep"])
    found["pitch_op"] = next(
        m.captures["op"]
        for m in find_code_all(image, PITCH_OP)
        if m.captures["acc"] == found["program_acc"]
    )
    return found


def entries(view):
    """Every Music Assembler play entry in ``view``."""
    return {m.addr for m in layout.play_entries(view)}


def row_reader(view, found) -> int:
    """The address of the row reader: the ``BMI`` target after ``DEC dur,X``."""
    hit = next(
        m
        for m in find_code_all(_image(view), ADVANCE)
        if m.captures["dur"] == found["dur"]
    )
    offset = hit.captures["offset"]
    return hit.addr + 5 + (offset - 0x100 if offset & 0x80 else offset)
