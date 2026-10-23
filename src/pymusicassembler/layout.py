"""Locate every Music Assembler work cell and table from the player code itself.

Builds differ in load address and in layout (later builds insert orderlist and
wave-program commands), so each address is read from the operand of the
instruction that uses it, in the routine the play entry's control flow reaches.
"""

from dataclasses import dataclass, fields
from typing import Iterable, Optional, Tuple

from pysidtracker import OP_LEN, CodePattern, Match, SidImage, find_code_all

from pymusicassembler import constants
from pymusicassembler.errors import MusicAssemblerParseError

JMP_ABS = 0x4C
JSR_ABS = 0x20
STA_ABS_X = 0x9D
CMP_ABS_X = 0xDD
_RETURNS = (0x00, 0x40, 0x60, 0x6C)  # BRK, RTI, RTS, JMP (ind)
IRQ_VECTORS = (0x0314, 0xFFFE)

PLAY_ENTRY = CodePattern("A2 00 CE {tempo:w} 30 {bmi}")

# Each routine is matched where the player's own control flow reaches it: a
# ``{to_*}`` branch offset or ``{*:w}`` call target captured by an earlier one,
# or ``*_end`` straight after one, through any ``JMP`` trampoline. ``{at_*}``
# records where a self-modified immediate operand lives.
_ROUTINES = (
    ("entry", "A2 00 CE {tempo:w} 30 {to_reload} 20 {output:w}"),
    ("to_reload", "A9 {at_tempo_reload} 8D ?? ?? 20 {advance:w}"),
    ("advance", "DE {dur:w}"),
    ("advance_end", "30 {to_parse}"),
    (
        "to_parse",
        "BC {pattern_id:w} C0 FE D0 09 BD {gate_ctrl:w} 29 FE 9D ?? ?? 60 B9 {pattern_lo:w}"
        " 85 ?? B9 {pattern_hi:w} 85 ?? BC {pat_cursor:w} B1 ?? 30 20 C9 60"
        " 90 {to_note} 29 1F 9D ?? ?? A9 FE 9D {gate_mask:w} 20 ?? ?? 4C {row_end:w}",
    ),
    (
        "to_note",
        "85 ?? C8 BD {transrep:w} 4A 4A 4A 4A 18 65 ?? 9D {note_index:w} 84 ?? A8"
        " B9 {freq_lo:w} 9D {freq_lo_w:w} 9D {vib_freq_lo:w} B9 {freq_hi:w}"
        " 9D {freq_hi_w:w} 9D {vib_freq_hi:w} A4 ?? B1 ?? 9D {note_ctl:w} 29 1F"
        " 9D ?? ?? B1 ?? 30 {to_filter} 29 20 F0 ?? C8 B1 ?? 9D {vib_lo_step:w}"
        " C8 B1 ?? 9D {vib_hi_step:w}",
    ),
    (
        "to_filter",
        "8E {filter_owner:w} C8 B1 ?? 8D {fc_acc_reset:w} 29 0F 0A 38 E9 10"
        " 8D {fc_step:w} C8 B1 ?? D0 07 A9 F0 ?? 17 ?? ?? 09 8D {fc_ctr_reset:w}"
        " BD {res_presets:w} 8D 17 {res_sid} A9 FF 9D {ctrl_mask:w} 9D {vib_toggle:w}"
        " A9 00 9D {arp_substep:w} 9D {arp_phase:w}",
    ),
    (
        "row_end",
        "C8 B1 ?? C9 FF D0 ?? DE {repeat:w} 10 ?? BD {order_lo:w} 85 ??"
        " BD {order_hi:w} 85 ?? BC {order_cursor:w} C8",
    ),
    (
        "output",
        "BC {instr_x8:w} 84 ?? BD ?? ?? 29 40 D0 {to_sustain} 9D {instr_cursor:w}"
        " B9 {instr:w} 85 ?? B9 ?? ?? BC {voice_reg:w} 99 06 {sid} A5 ?? 99 05 ??"
        " BD {ctrl:w} 29 FE 99 04 ?? A4 ?? B9 ?? ?? 9D ?? ?? B9 ?? ?? 9D {pw_lo:w}"
        " 9D {pw_hi:w} E0 {at_trigger_owner} D0 0A A9 {at_trigger_acc} 8D {fc_acc:w}"
        " A9 {at_trigger_ctr} 8D {fc_ctr:w} A9 00"
        " 9D {pw_substep:w} 9D {pw_dir:w} B9 ?? ?? 4A 4A 4A 9D {porta_ctr:w}"
        " BD ?? ?? 09 40 9D ?? ?? B9 ?? ?? 95 {flags}",
    ),
    (
        "to_sustain",
        "EC {sweep_owner:w} D0 12 A9 {at_sweep_ctr} F0 0E CE {sweep_dec:w} 18"
        " A9 {at_sweep_acc} 69 {at_sweep_step} 8D {sweep_sta:w} 8D 16 {fc_sid}"
        " B5 ?? 29 0F F0 {to_arp} 20 {program:w}",
    ),
    ("to_sustain_end", "A4 ?? B9 ?? ?? 85 ?? B5 ?? 29 40 F0 {to_pulse_sweep}"),
    (
        "to_arp",
        "BD ?? ?? 29 20 D0 ?? B5 ?? 29 10 F0 ?? DE {at_arp_porta_dec} ?? 10 ??"
        " FE {at_arp_porta_inc} ?? BD {at_arp_phase_rd} ?? 29 03 A8 B9 {arp:w} D0 13"
        " A4 ?? 38 BD {at_arp_down_src} ?? F9 {at_arp_down_step} ?? {arp_down_op}"
        " {at_arp_down_dst} ?? B0 18 DE {at_arp_down_hi} ?? D0 13 A4 ?? 18"
        " BD {at_arp_up_src} ?? 79 {at_arp_up_step} ?? 9D {at_arp_up_dst} ?? 90 05"
        " FE {at_arp_up_hi} ?? B0 00 FE {at_arp_substep_inc} ?? B9 {at_arp_speed} ??"
        " 29 0F DD {at_arp_substep_cmp} ?? D0 08 A9 00 9D {at_arp_substep_clr} ??"
        " FE {at_arp_phase_inc} ??",
    ),
    (
        "program",
        "A8 B9 {inner_lo:w} 85 ?? B9 {inner_hi:w} 85 ?? BC ?? ?? B1 ?? 3D ?? ??"
        " 9D ?? ?? C8 B1 ?? 30 {pitch_skip} 18 7D ?? ?? 29 7F 8D ?? ?? C8 B1 ??"
        " F0 03 8D {program_acc:w} C8 B1 ?? C9",
    ),
)
_ROUTINES = tuple((at, CodePattern(spec)) for at, spec in _ROUTINES)

_INIT = CodePattern(
    "A9 ?? 8D 18 ?? A9 ?? 8D 17 ?? 29 0F 8D ?? ?? A2 0F 9D ?? ?? CA 10 FA A2 02"
)

# Work cell holding the orderlist transrep byte, distinct from the transpose read cell.
TRANSREP_BYTE = CodePattern("9D {cell:w} 29 0F 9D {repeat:w}")

# The output routine's vibrato step: the accumulators it adds the note's steps to
# (``vib_freq_lo``/``hi`` but for builds whose note routine stores elsewhere).
VIB_OUT = CodePattern(
    "BD {step:w} 29 01 F0 ?? BD {toggle:w} 49 FF 9D ?? ?? D0 ?? 18 BD {lo:w}"
    " 7D ?? ?? 9D ?? ?? 99 00 ?? BD {hi:w}"
)

# Optional commands later builds decode: (feature, pattern, captured cell that
# must equal the named Layout cell, so a match belongs to this player instance).
_FEATURES = (
    (
        "order_tempo",
        CodePattern("C9 FC D0 ?? C8 B1 ?? 8D {cell:w} 8D ?? ?? C8 4C"),
        "tempo_reload",
    ),
    (
        "order_jump",
        CodePattern("C9 FD D0 ?? C8 B1 ?? 0A 9D {cell:w} A8"),
        "order_cursor",
    ),
    (
        "order_jump",
        CodePattern("C9 FD F0 01 60 C8 B1 ?? 0A 9D {cell:w} A8"),
        "order_cursor",
    ),
)

# The program routine's ``$FD r`` jump, at its marker compare: bounds ``r`` by
# ``cell,X + 3`` (``instr_cursor`` but for rips that left the operand unrelocated).
PROGRAM_JUMP_CODE = CodePattern("C9 FD D0 ?? C8 BD {cell:w} 18 69 03 8D")

PROGRAM_JUMP = constants.PROGRAM_JUMP


@dataclass(frozen=True)
class Layout:
    """Absolute addresses of one player instance's code, work cells and tables."""

    # pylint: disable=too-many-instance-attributes  # one field per player cell
    entry: int
    tempo: int
    tempo_reload: int
    init: int
    order_lo: int
    order_hi: int
    pattern_id: int
    transrep: int
    transrep_byte: int
    repeat: int
    ctrl: int
    gate_ctrl: int
    pattern_lo: int
    pattern_hi: int
    pat_cursor: int
    dur: int
    ctrl_mask: int
    gate_mask: int
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
    vib_out_lo: int
    vib_out_hi: int
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
    trigger_owner: int
    trigger_acc: int
    trigger_ctr: int
    sweep_owner: int
    sweep_ctr: int
    sweep_dec: int
    sweep_acc: int
    sweep_step: int
    sweep_sta: int
    program_acc: int
    pw_substep: int
    pw_dir: int
    porta_ctr: int
    flags: int
    arp: int
    # arp_* (but arp_down_cmp): where the arpeggio code's abs,X/abs,Y operands live
    arp_porta_dec: int
    arp_porta_inc: int
    arp_phase_rd: int
    arp_down_src: int
    arp_down_step: int
    arp_down_dst: int
    arp_down_cmp: bool
    arp_down_hi: int
    arp_up_src: int
    arp_up_step: int
    arp_up_dst: int
    arp_up_hi: int
    arp_substep_inc: int
    arp_speed: int
    arp_substep_cmp: int
    arp_substep_clr: int
    arp_phase_inc: int
    inner_lo: int
    inner_hi: int
    pitch_skip: int
    sid: int
    res_sid: int
    fc_sid: int
    order_tempo: bool = False
    order_jump: bool = False
    program_jump: int = 0
    pulse_always: bool = False
    output_voices: Tuple[int, ...] = (0, 1, 2)


def _as_image(mem) -> SidImage:
    return mem if isinstance(mem, SidImage) else SidImage.from_prg(b"\0\0" + bytes(mem))


def play_entries(mem, start: int = 0, end: int = 0x10000) -> Iterable[Match]:
    """Every ``LDX #0; DEC tempo; BMI`` play entry in ``[start, end)``."""
    return find_code_all(_as_image(mem), PLAY_ENTRY, start=start, end=end)


def _word(image: SidImage, addr: int) -> int:
    return image.peek(addr) | (image.peek(addr + 1) << 8)


def _walk(image: SidImage, addr: int, entries, seen) -> Optional[int]:
    """The first play entry the code at ``addr`` jumps to or calls, depth first."""
    while addr not in seen:
        if addr in entries:
            return addr
        seen.add(addr)
        op = image.peek(addr)
        target = _word(image, addr + 1)
        if op == JMP_ABS:
            addr = target
            continue
        if op == JSR_ABS:
            found = _walk(image, target, entries, seen)
            if found is not None:
                return found
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
        found = _walk(image, addr, entries, set())
        if found is not None:
            return found
    return min(entries, key=lambda e: abs(e - starts[0]), default=None)


def _follow_jmp(image: SidImage, addr: int) -> int:
    """``addr``, or where the chain of ``JMP abs`` trampolines at it leads."""
    seen = set()
    while image.peek(addr) == JMP_ABS and addr not in seen:
        seen.add(addr)
        addr = _word(image, addr + 1)
    return addr


def _s8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


def _resolve_voice(
    image: SidImage, target: int, x: int, output: int
) -> Tuple[int, bool]:
    """``(x, reaches_output)``: follow ``target``'s ``INX``/``LDX #n``/``JMP``
    chain, updating ``x`` as each instruction would."""
    for _ in range(8):
        if target == output:
            return x, True
        op = image.peek(target)
        if op == 0xE8:
            x = (x + 1) & 0xFF
            target = (target + 1) & 0xFFFF
        elif op == 0xA2:
            x = image.peek(target + 1)
            target = (target + 2) & 0xFFFF
        elif op == JMP_ABS:
            target = _word(image, target + 1)
        else:
            return x, False
    return x, False


def _output_voices(image: SidImage, entry: int, output: int) -> Tuple[int, ...]:
    """The voices the play entry re-outputs on a non-underflow call.

    Walks the dispatch from the entry's ``JSR output`` (``X = 0``); ``LDX #n``
    / ``INX`` set the next voice, each call resolvable to ``output`` re-outputs
    it, and ``JMP`` or an unrecognised instruction stops the walk.
    """
    voices = []
    x = 0
    pc = entry + 7
    for _ in range(16):
        op = image.peek(pc)
        if op == 0xA2:
            x = image.peek(pc + 1)
            pc = (pc + 2) & 0xFFFF
            continue
        if op == 0xE8:
            x = (x + 1) & 0xFF
            pc += 1
            continue
        if op in (JSR_ABS, JMP_ABS):
            voice, is_output = _resolve_voice(image, _word(image, pc + 1), x, output)
            if not is_output:
                break
            voices.append(voice)
            if op == JSR_ABS:
                x = voice
                pc = (pc + 3) & 0xFFFF
                continue
            break
        break
    return tuple(voices)


def discover(mem, entry: int, start: int = 0, end: int = 0x10000) -> Layout:
    """The :class:`Layout` of the player whose play entry is at ``entry``.

    Raises :class:`MusicAssemblerParseError` when a routine is not where the
    player's control flow leads.
    """
    image = _as_image(mem)
    found = {"entry": entry}
    for at, pattern in _ROUTINES:
        addr = _follow_jmp(image, found[at])
        hit = find_code_all(image, pattern, start=addr, end=addr + pattern.length)
        if not hit:
            raise MusicAssemblerParseError(
                f"player signatures not found ({at} at {addr:#06x}); unsupported variant"
            )
        for name, offset, _ in pattern.captures:
            value = hit[0].captures[name]
            if name[:3] == "to_":
                value = addr + offset + 1 + _s8(value)
            elif name[:3] == "at_":
                name, value = name[3:], addr + offset
            found[name] = value
        found[f"{at}_end"] = addr + pattern.length
    found["pulse_always"] = found["to_pulse_sweep"] == found["to_sustain_end_end"]
    if found["arp_down_op"] not in (STA_ABS_X, CMP_ABS_X):
        raise MusicAssemblerParseError("player signatures not found (arp descent)")
    found["arp_down_cmp"] = found["arp_down_op"] == CMP_ABS_X
    for page in ("sid", "res_sid", "fc_sid"):
        found[page] <<= 8
    if found["pitch_skip"] not in (0, 4):
        raise MusicAssemblerParseError("player signatures not found (pitch_skip)")
    inits = find_code_all(image, _INIT, start=start, end=end)
    found["init"] = min(
        (m.addr for m in inits), key=lambda a: abs(a - entry), default=0
    )
    for feature, pattern, cell in _FEATURES:
        if any(
            m.captures["cell"] == found[cell]
            for m in find_code_all(image, pattern, start=start, end=end)
        ):
            found[feature] = True
    at = found["program_end"] - 1
    hit = find_code_all(
        image, PROGRAM_JUMP_CODE, start=at, end=at + PROGRAM_JUMP_CODE.length
    )
    found["program_jump"] = hit[0].captures["cell"] if hit else 0
    for hit in find_code_all(image, TRANSREP_BYTE, start=start, end=end):
        if hit.captures["repeat"] == found["repeat"]:
            found["transrep_byte"] = hit.captures["cell"]
            break
    else:
        found["transrep_byte"] = found["transrep"]
    found["vib_out_lo"], found["vib_out_hi"] = next(
        (
            (m.captures["lo"], m.captures["hi"])
            for m in find_code_all(image, VIB_OUT, start=start, end=end)
            if (m.captures["step"], m.captures["toggle"])
            == (found["vib_lo_step"], found["vib_toggle"])
        ),
        (found["vib_freq_lo"], found["vib_freq_hi"]),
    )
    found["output_voices"] = _output_voices(image, entry, found["output"])
    return Layout(**{f.name: found[f.name] for f in fields(Layout) if f.name in found})
