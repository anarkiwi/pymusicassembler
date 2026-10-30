"""The Music Assembler playroutine as a ``pysidtracker.playspec`` spec.

``specs/musicassembler.play`` states the play call; :func:`binding` resolves its
``layout`` from :mod:`pymusicassembler.layout` discovery plus the three cells the
play code fixes in zero page and its self-modified pitch operand. A build whose
order step calls a rip's own routine runs ``specs/musicassembler_hook.play``, the
variant that hands the byte to that routine; :func:`bind` states that routine as
spec statements when it can, so the call needs no machine code.
"""

import dataclasses
from importlib import resources

from pysidtracker import CodePattern, SidImage, find_code_all
from pysidtracker.errors import SidError
from pysidtracker.playspec import load
from pysidtracker.playspec.unnative import count_natives, unnative

from pymusicassembler import layout

_SPECS = resources.files(__package__).joinpath("specs")
SPEC = load(_SPECS.joinpath("musicassembler.play"))
HOOK_SPEC = load(_SPECS.joinpath("musicassembler_hook.play"))

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


def bind(view, entry):
    """``(spec, binding)`` of the instance at ``entry``: the hook variant when its
    order step calls a rip's routine, the base spec otherwise.

    The hook variant has the rip's routine lifted to spec statements
    (``unnative``); a routine the spec cannot state (it reads registers it was not
    handed, the stack or code) stays a ``native`` call run on the CPU.
    """
    found = binding(view, entry)
    if found["order_native"]:
        try:
            return unnative(HOOK_SPEC, found, bytes(view)), found
        except SidError:
            return HOOK_SPEC, found
    found.pop("order_native")
    found.pop("order_resume")
    return SPEC, found


def has_native(spec) -> bool:
    """Whether ``spec`` still holds a ``native`` statement: a tune bound to it runs
    machine code the spec does not state, so it is not played from standard notes."""
    return bool(count_natives(spec.machines) + count_natives(spec.procs))


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
