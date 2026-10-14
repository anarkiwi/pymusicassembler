"""Locate every Music Assembler work cell and table from the player code itself.

Builds differ in load address and in layout (later builds insert orderlist and
wave-program commands), so each address is read from the operand of the
instruction that uses it, matched by opcode skeleton nearest the play entry.
"""

from dataclasses import dataclass, fields
from typing import Iterable, Optional

from pysidtracker import OP_LEN, CodePattern, Match, SidImage, find_code_all

from pymusicassembler.errors import MusicAssemblerParseError

JMP_ABS = 0x4C
JSR_ABS = 0x20
_RETURNS = (0x00, 0x40, 0x60, 0x6C)  # BRK, RTI, RTS, JMP (ind)
IRQ_VECTORS = (0x0314, 0xFFFE)

PLAY_ENTRY = CodePattern("A2 00 CE {tempo:w} 30 {bmi}")
_RELOAD = CodePattern("A9 ?? 8D {tempo:w}")

_SIGNATURES = (
    CodePattern(
        "A9 ?? 8D 18 D4 A9 ?? 8D 17 D4 29 0F 8D {owner_init:w} A2 0F 9D {clear:w}"
        " CA 10 FA A2 02 BD {order_lo:w} 85 FA BD {order_hi:w} 85 FB A0 00 B1 FA"
        " 9D {pattern_id:w} C8 B1 FA 9D {transrep:w} 29 0F 9D {repeat:w}"
    ),
    CodePattern(
        "BC ?? ?? C0 FE D0 09 BD {ctrl:w} 29 FE 9D ?? ?? 60 B9 {pattern_lo:w} 85 FA"
        " B9 {pattern_hi:w} 85 FB BC {pat_cursor:w} B1 FA 30 20 C9 60 90 43 29 1F"
        " 9D {dur:w} A9 FE 9D {ctrl_mask:w}"
    ),
    CodePattern(
        "0A 0A 0A 9D {instr_x8:w} C8 B1 FA C9 60 B0 BD 85 FC C8 BD ?? ?? 4A 4A 4A 4A"
        " 18 65 FC 9D {note_index:w} 84 FC A8 B9 {freq_lo:w} 9D {freq_lo_w:w}"
        " 9D {vib_freq_lo:w} B9 {freq_hi:w} 9D {freq_hi_w:w} 9D {vib_freq_hi:w}"
        " A4 FC B1 FA 9D {note_ctl:w} 29 1F 9D ?? ?? B1 FA 30 22 29 20 F0 45 C8 B1 FA"
        " 9D {vib_lo_step:w} C8 B1 FA 9D {vib_hi_step:w}"
    ),
    CodePattern(
        "8E {filter_owner:w} C8 B1 FA 8D {fc_acc_reset:w} 29 0F 0A 38 E9 10"
        " 8D {fc_step:w} C8 B1 FA D0 07 A9 F0 8D 17 D4 D0 09 8D {fc_ctr_reset:w}"
        " BD {res_presets:w} 8D 17 D4 A9 FF 9D ?? ?? 9D {vib_toggle:w} A9 00"
        " 9D {arp_substep:w} 9D {arp_phase:w}"
    ),
    CodePattern(
        "C8 B1 FA C9 FF D0 ?? DE ?? ?? 10 ?? BD ?? ?? 85 FA BD ?? ?? 85 FB"
        " BC {order_cursor:w} C8"
    ),
    CodePattern(
        "BC ?? ?? 84 FC BD ?? ?? 29 40 D0 ?? 9D {instr_cursor:w} B9 {instr:w} 85 FA"
        " B9 ?? ?? BC {voice_reg:w} 99 06 D4 A5 FA 99 05 D4 BD ?? ?? 29 FE 99 04 D4"
        " A4 FC B9 ?? ?? 9D ?? ?? B9 ?? ?? 9D {pw_lo:w} 9D {pw_hi:w} E0 ?? D0 0A"
        " A9 ?? 8D {fc_acc:w} A9 ?? 8D {fc_ctr:w} A9 00 9D {pw_substep:w}"
        " 9D {pw_dir:w} B9 ?? ?? 4A 4A 4A 9D {porta_ctr:w} BD ?? ?? 09 40 9D ?? ??"
        " B9 ?? ?? 95 {flags}"
    ),
    CodePattern("DE ?? ?? 10 49 FE ?? ?? BD ?? ?? 29 03 A8 B9 {arp:w} D0 13"),
    CodePattern(
        "A8 B9 {inner_lo:w} 85 FA B9 {inner_hi:w} 85 FB BC ?? ?? B1 FA 3D ?? ??"
        " 9D ?? ?? C8 B1 FA 30 {pitch_skip} 18 7D ?? ?? 29 7F 8D ?? ?? C8 B1 FA F0 03"
        " 8D ?? ?? C8 B1 FA C9"
    ),
)

# Optional commands later builds decode: (feature, pattern, captured cell that
# must equal the named Layout cell, so a match belongs to this player instance).
_FEATURES = (
    (
        "order_tempo",
        CodePattern("C9 FC D0 ?? C8 B1 FA 8D {cell:w} 8D ?? ?? C8 4C"),
        "tempo_reload",
    ),
    (
        "order_jump",
        CodePattern("C9 FD D0 ?? C8 B1 FA 0A 9D {cell:w} A8"),
        "order_cursor",
    ),
    (
        "order_jump",
        CodePattern("C9 FD F0 01 60 C8 B1 FA 0A 9D {cell:w} A8"),
        "order_cursor",
    ),
    (
        "program_jump",
        CodePattern("C9 FD D0 ?? C8 BD {cell:w} 18 69 03 8D"),
        "instr_cursor",
    ),
)

PROGRAM_JUMP = 0xFD


@dataclass(frozen=True)
class Layout:
    """Absolute addresses of one player instance's code, work cells and tables."""

    # pylint: disable=too-many-instance-attributes  # one field per player cell
    entry: int
    tempo: int
    tempo_reload: int
    init: int
    owner_init: int
    clear: int
    order_lo: int
    order_hi: int
    pattern_id: int
    transrep: int
    repeat: int
    ctrl: int
    pattern_lo: int
    pattern_hi: int
    pat_cursor: int
    dur: int
    ctrl_mask: int
    instr_x8: int
    note_index: int
    freq_lo: int
    freq_lo_w: int
    vib_freq_lo: int
    freq_hi: int
    freq_hi_w: int
    vib_freq_hi: int
    note_ctl: int
    vib_lo_step: int
    vib_hi_step: int
    filter_owner: int
    fc_acc_reset: int
    fc_step: int
    fc_ctr_reset: int
    res_presets: int
    vib_toggle: int
    arp_substep: int
    arp_phase: int
    order_cursor: int
    instr_cursor: int
    instr: int
    voice_reg: int
    pw_lo: int
    pw_hi: int
    fc_acc: int
    fc_ctr: int
    pw_substep: int
    pw_dir: int
    porta_ctr: int
    flags: int
    arp: int
    inner_lo: int
    inner_hi: int
    pitch_skip: int
    order_tempo: bool = False
    order_jump: bool = False
    program_jump: bool = False


def _as_image(mem) -> SidImage:
    return mem if isinstance(mem, SidImage) else SidImage.from_prg(b"\0\0" + bytes(mem))


def play_entries(mem, start: int = 0, end: int = 0x10000) -> Iterable[Match]:
    """Every ``LDX #0; DEC tempo; BMI`` play entry in ``[start, end)``."""
    return find_code_all(_as_image(mem), PLAY_ENTRY, start=start, end=end)


def _word(image: SidImage, addr: int) -> int:
    return image.peek(addr) | (image.peek(addr + 1) << 8)


def _walk(image: SidImage, addr: int, entries) -> Optional[int]:
    """The play entry the code at ``addr`` calls or jumps to before it returns."""
    seen = set()
    while addr not in seen:
        if addr in entries:
            return addr
        seen.add(addr)
        op = image.peek(addr)
        target = _word(image, addr + 1)
        if op == JMP_ABS:
            addr = target
            continue
        if op == JSR_ABS and target in entries:
            return target
        if op in _RETURNS:
            return None
        addr = (addr + OP_LEN[op]) & 0xFFFF
    return None


def resolve_entry(mem, play: int, start: int = 0, end: int = 0x10000) -> Optional[int]:
    """The play entry the play routine reaches, else the one nearest to it.

    ``play`` 0 starts from the IRQ vectors init installed. ``None`` when
    ``[start, end)`` holds no play entry at all.
    """
    image = _as_image(mem)
    entries = {m.addr for m in play_entries(image, start, end)}
    starts = [play] if play else [_word(image, v) for v in IRQ_VECTORS]
    for addr in starts:
        found = _walk(image, addr, entries)
        if found is not None:
            return found
    return min(entries, key=lambda e: abs(e - starts[0]), default=None)


def _nearest(image: SidImage, pattern: CodePattern, entry: int, start, end):
    matches = find_code_all(image, pattern, start=start, end=end)
    return min(matches, key=lambda m: abs(m.addr - entry), default=None)


def discover(mem, entry: int, start: int = 0, end: int = 0x10000) -> Layout:
    """The :class:`Layout` of the player whose play entry is at ``entry``.

    Raises :class:`MusicAssemblerParseError` when a signature is missing.
    """
    image = _as_image(mem)
    head = find_code_all(image, PLAY_ENTRY, start=entry, end=entry + 7)
    if not head:
        raise MusicAssemblerParseError(f"no play entry at {entry:#06x}")
    tempo = head[0].captures["tempo"]
    target = entry + 7 + head[0].captures["bmi"]
    reload = find_code_all(image, _RELOAD, start=target, end=target + 5)
    if not reload or reload[0].captures["tempo"] != tempo:
        raise MusicAssemblerParseError(f"no tempo reload at {target:#06x}")
    found = {"entry": entry, "tempo": tempo, "tempo_reload": target + 1}
    missing = []
    for pattern in _SIGNATURES:
        match = _nearest(image, pattern, entry, start, end)
        if match is None:
            missing.append(pattern.captures[0][0])
            continue
        found.update(match.captures)
        if "owner_init" in match.captures:
            found["init"] = match.addr
    if found.get("pitch_skip", 0) not in (0, 4):
        missing.append("pitch_skip")
    if missing:
        raise MusicAssemblerParseError(
            f"player signatures not found ({','.join(missing)}); unsupported variant"
        )
    for feature, pattern, cell in _FEATURES:
        if any(
            m.captures["cell"] == found[cell]
            for m in find_code_all(image, pattern, start=start, end=end)
        ):
            found[feature] = True
    return Layout(**{f.name: found[f.name] for f in fields(Layout) if f.name in found})
