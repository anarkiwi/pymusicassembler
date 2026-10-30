"""Player-cell discovery from the player code (:mod:`pymusicassembler.layout`)."""

from dataclasses import replace

import pytest

from pysidtracker import CodePattern, SidImage

from pymusicassembler import Player, layout, reader
from pymusicassembler.constants import INSTR_ARP_SPEED, INSTR_ARP_STEP
from pymusicassembler.errors import MusicAssemblerParseError

SPACE = {
    "entry": 0x1021,
    "tempo": 0x1090,
    "tempo_reload": 0x1035,
    "init": 0x1048,
    "order_lo": 0x14B9,
    "order_hi": 0x14BC,
    "pattern_lo": 0x1532,
    "pattern_hi": 0x152E,
    "ctrl_mask": 0x1031,
    "freq_lo": 0x1437,
    "freq_hi": 0x11C5,
    "freq_lo_w": 0x10CC,
    "freq_hi_w": 0x10CF,
    "note_ctl": 0x1141,
    "filter_owner": 0x1262,
    "fc_acc_reset": 0x1266,
    "fc_ctr_reset": 0x126B,
    "fc_acc": 0x129E,
    "fc_ctr": 0x1296,
    "fc_step": 0x12A0,
    "trigger_owner": 0x1262,
    "trigger_acc": 0x1266,
    "trigger_ctr": 0x126B,
    "sweep_owner": 0x1262,
    "sweep_ctr": 0x1296,
    "sweep_dec": 0x1296,
    "sweep_acc": 0x129E,
    "sweep_step": 0x12A0,
    "sweep_sta": 0x129E,
    "program_acc": 0x129E,
    "sid": 0xD400,
    "res_sta": 0xD417,
    "fc_sid": 0xD400,
    "res_presets": 0x12B3,
    "voice_reg": 0x10C6,
    "arp": 0x12C3,
    "instr": 0x1536,
    "inner_lo": 0x1498,
    "inner_hi": 0x14A8,
    "flags": 0xFD,
    "pitch_skip": 4,
}


def _layout(path):
    image = SidImage.from_bytes(path.read_bytes())
    return Player(reader.parse(path.read_bytes())).layout, image


def test_standard_build_cells(tune_path):
    lay, _ = _layout(tune_path)
    if "Space_Invadarz" in tune_path.name:
        assert {k: getattr(lay, k) for k in SPACE} == SPACE
    assert lay.freq_hi_w == lay.freq_lo_w + 3
    assert lay.tempo_reload == lay.entry + 0x14
    assert not lay.order_tempo and not lay.program_jump


def test_order_jump_hook_build(tune_path):
    lay, _ = _layout(tune_path)
    assert bool(lay.order_jump) == ("3LUX" in tune_path.name)


def test_later_build_features(later_tune_path):
    lay, image = _layout(later_tune_path)
    assert lay.order_tempo and lay.order_jump
    assert lay.program_jump == lay.instr_cursor
    assert lay.init < lay.entry < lay.tempo
    assert image.peek(lay.tempo_reload - 1) == 0xA9


def _mem(code):
    mem = bytearray(0x10000)
    for addr, data in code.items():
        mem[addr : addr + len(data)] = bytes(data)
    return mem


ENTRY = (0xA2, 0x00, 0xCE, 0x90, 0x10, 0x30, 0x0C)


@pytest.mark.parametrize(
    "play,expected",
    [
        (0x2000, 0x1021),
        (0x2010, 0x1021),
        (0x2020, 0x1021),
        (0, 0x1021),
        (0x3000, 0x3021),
        (0x2030, 0x3021),
        (0x2040, 0x1021),
    ],
)
def test_resolve_entry_walks_play_routines(play, expected):
    mem = _mem(
        {
            0x1021: ENTRY,
            0x3021: ENTRY,
            0x2000: (0x4C, 0x10, 0x20),
            0x2010: (0xA9, 0x35, 0x85, 0x01, 0x20, 0x21, 0x10, 0x60),
            0x2020: (0x20, 0x00, 0x40, 0x4C, 0x21, 0x10),
            0x2030: (0x4C, 0x30, 0x20),
            0x2040: (0x20, 0x50, 0x20, 0x60),
            0x2050: (0xEA, 0x4C, 0x21, 0x10),
            0x3000: (0x60,),
            0x0314: (0x10, 0x20),
        }
    )
    assert layout.resolve_entry(mem, play) == expected


def test_resolve_entry_without_player():
    assert layout.resolve_entry(bytes(0x10000), 0x1000) is None


def test_discover_needs_entry_and_reload(tune_path):
    song = reader.read(tune_path)
    view = reader.load_view(song.image, song.load_addr)
    entry = layout.resolve_entry(view, song.play_addr)
    with pytest.raises(MusicAssemblerParseError, match=r"\(entry at"):
        layout.discover(view, entry + 1)
    image = bytearray(song.image)
    image[entry + 0x13 - song.load_addr] = 0xEA
    with pytest.raises(MusicAssemblerParseError, match=r"\(to_reload at"):
        layout.discover(reader.load_view(bytes(image), song.load_addr), entry)


def test_discover_rejects_unknown_pitch_branch(tune_path):
    song = reader.read(tune_path)
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0xC8, 0xB1, 0xFA, 0x30, 0x04, 0x18, 0x7D))) + 4
    image[at] = 0x02
    view = reader.load_view(bytes(image), song.load_addr)
    with pytest.raises(MusicAssemblerParseError, match="pitch_skip"):
        layout.discover(view, layout.resolve_entry(view, song.play_addr))


def test_routines_are_found_where_control_flow_leads(tune_path):
    """A closer copy of a routine with other operands is not mistaken for it."""
    song = reader.read(tune_path)
    mem = bytearray(Player(song).memory)
    lay = layout.discover(mem, Player(song).layout.entry)
    tick = mem.index(bytes((0xA8, 0xB9, lay.inner_lo & 0xFF, lay.inner_lo >> 8)))
    decoy = song.load_addr - 0x40
    mem[decoy : decoy + 0x30] = mem[tick : tick + 0x30]
    mem[decoy + 2] ^= 0x55
    assert layout.discover(mem, lay.entry) == lay


def test_routines_are_followed_through_jmp_trampolines(tune_path):
    song = reader.read(tune_path)
    mem = bytearray(Player(song).memory)
    lay = layout.discover(mem, Player(song).layout.entry)
    call = lay.tempo_reload + 5
    advance = mem[call] | (mem[call + 1] << 8)
    mem[0xF000:0xF006] = bytes((0x4C, 0x03, 0xF0, 0x4C, advance & 0xFF, advance >> 8))
    mem[call : call + 2] = bytes((0x00, 0xF0))
    assert layout.discover(mem, lay.entry) == lay


def test_tempo_reload_read_past_jmp_trampoline(tune_path):
    """The reload immediate is found where the entry's ``BMI`` trampoline leads."""
    song = reader.read(tune_path)
    mem = bytearray(Player(song).memory)
    lay = layout.discover(mem, Player(song).layout.entry)
    reload = lay.tempo_reload - 1
    mem[0xF000:0xF008] = mem[reload : reload + 8]
    mem[reload : reload + 3] = bytes((0x4C, 0x00, 0xF0))
    assert layout.discover(mem, lay.entry) == replace(lay, tempo_reload=0xF001)


def test_pulse_mode_branch(tune_path):
    """A build whose ``AND #$40; BEQ`` falls through always adds the pulse step."""
    data = bytearray(tune_path.read_bytes())
    image = SidImage.from_bytes(bytes(data))
    lay = layout.discover(image, Player(reader.parse(bytes(data))).layout.entry)
    assert not lay.pulse_always
    at = image.find_code(
        CodePattern("A4 ?? B9 ?? ?? 85 ?? B5 ?? 29 40 F0"), start=lay.entry
    )
    image.poke(at.addr + 12, 0)
    assert layout.discover(image, lay.entry).pulse_always


def test_vibrato_output_cells_are_read_from_the_output_routine(tune_path):
    """A note routine storing its vibrato start elsewhere leaves the output's cells."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(image, Player(reader.read(tune_path)).layout.entry)
    assert (lay.vib_out_lo, lay.vib_out_hi) == (lay.vib_freq_lo, lay.vib_freq_hi)
    at = image.find_code(
        CodePattern(f"9D {lay.vib_freq_lo & 0xFF:02X} {lay.vib_freq_lo >> 8:02X} B9"),
        start=lay.entry,
    )
    image.poke(at.addr + 2, 0x01)
    patched = layout.discover(image, lay.entry)
    assert patched.vib_freq_lo == (lay.vib_freq_lo & 0xFF) | 0x0100
    assert (patched.vib_out_lo, patched.vib_out_hi) == (lay.vib_out_lo, lay.vib_out_hi)


def test_filter_stores_keep_their_own_sid_page(tune_path):
    """A build buffering the voice registers still stores the filter on the chip."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(
        image, Player(reader.parse(tune_path.read_bytes())).layout.entry
    )
    for pattern in ("99 06 {page}", "99 05 {page}", "99 04 {page}"):
        at = image.find_code(CodePattern(pattern), start=lay.entry)
        image.poke(at.addr + 2, 0x1C)
    moved = layout.discover(image, lay.entry)
    assert (moved.sid, moved.res_sta, moved.fc_sid) == (0x1C00, 0xD417, lay.sid)
    at = image.find_code(CodePattern("8D 16 {page}"), start=lay.entry)
    image.poke(at.addr + 2, 0xD5)
    assert layout.discover(image, lay.entry).fc_sid == 0xD500


@pytest.mark.parametrize(
    "lo, res_sta", [(0x17, 0xD417), (0x30, 0xD410), (0x5E, 0xD41E)]
)
def test_resonance_store_lands_on_the_register_it_aliases(tune_path, lo, res_sta):
    """A build storing the resonance to a SID mirror writes the register it aliases."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    entry = Player(reader.parse(tune_path.read_bytes())).layout.entry
    at = image.find_code(CodePattern("8D 17 D4 A9 FF"), start=entry)
    image.poke(at.addr + 1, lo)
    assert layout.discover(image, entry).res_sta == res_sta


def test_a_rips_own_order_hook_is_found_with_its_resume_address(tune_path):
    """A rip that replaces the step's end-marker compare with a ``JMP`` to its own
    routine names that routine, and the code after the compare it replaced."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    found = Player(reader.parse(tune_path.read_bytes())).layout
    if found.variant.entries["order_step"].value != "plain":
        pytest.skip("this build's step calls the order hook")
    entry = found.entry
    at = image.find_code(CodePattern("C8 B1 ?? C9 ?? D0 02 A0 ??"), start=entry)
    for i, byte in enumerate((0x4C, 0x34, 0x12, 0xEA)):
        image.poke(at.addr + 3 + i, byte)
    lay = layout.discover(image, entry)
    assert (lay.order_native, lay.order_resume) == (0x1234, at.addr + 9)
    assert lay.variant.entries["order_step"].value == "native"


def test_a_standard_step_has_no_order_hook(tune_path):
    lay = Player(reader.read(tune_path)).layout
    assert (lay.order_native, lay.order_resume) == (0, 0)


def test_pulse_sweep_names_its_standard_cells(tune_path):
    """A standard build's sweep compares the cell it counts in, against ``AND #$0F``."""
    lay = Player(reader.read(tune_path)).layout
    assert lay.pw_substep_cmp == lay.pw_substep
    assert not lay.pulse_cmp_adc


@pytest.mark.parametrize("limit, adc", [(0x29, False), (0x69, True)])
def test_pulse_sweep_reads_its_compare_operand_and_limit_opcode(tune_path, limit, adc):
    """A rip may leave the compare's operand unrelocated or its limit an ``ADC``."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    entry = Player(reader.parse(tune_path.read_bytes())).layout.entry
    at = image.find_code(CodePattern("A5 ?? 29 0F DD ?? ??"), start=entry)
    image.poke(at.addr + 2, limit)
    image.poke(at.addr + 5, 0xE0)
    image.poke(at.addr + 6, 0xA1)
    lay = layout.discover(image, entry)
    assert (lay.pw_substep_cmp, lay.pulse_cmp_adc) == (0xA1E0, adc)


def test_arp_operands_name_the_standard_cells(tune_path):
    """A standard build's arpeggio code reads the cells the rest of the player uses."""
    player = Player(reader.read(tune_path))
    lay = player.layout

    mem = player.memory

    def cell(name):
        at = getattr(lay, f"arp_{name}")
        return mem[at] | mem[at + 1] << 8

    assert {cell(n) for n in ("down_src", "down_dst", "up_src", "up_dst")} == {
        lay.freq_lo_w
    }
    assert cell("down_hi") == cell("up_hi") == lay.freq_hi_w
    assert cell("down_step") == cell("up_step") == lay.instr + INSTR_ARP_STEP
    assert cell("speed") == lay.instr + INSTR_ARP_SPEED
    assert cell("porta_dec") == cell("porta_inc") == lay.porta_ctr
    assert cell("phase_rd") == cell("phase_inc") == lay.arp_phase
    assert {cell(f"substep_{n}") for n in ("inc", "cmp", "clr")} == {lay.arp_substep}
    assert not lay.arp_down_cmp


@pytest.mark.parametrize("op,cmp", [(0xDD, True), (0x9D, False), (0x8D, None)])
def test_arp_descent_store_or_compare(tune_path, op, cmp):
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(image, Player(reader.read(tune_path)).layout.entry)
    image.poke(lay.arp_down_dst - 1, op)
    if cmp is None:
        with pytest.raises(MusicAssemblerParseError, match="arp descent"):
            layout.discover(image, lay.entry)
    else:
        assert layout.discover(image, lay.entry).arp_down_cmp is cmp


def test_gate_cells_are_read_from_the_parse_routine(tune_path):
    """A rip whose parse routine left its gate operands unrelocated keeps the
    output and note routines' ``ctrl``/``ctrl_mask``."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(image, Player(reader.read(tune_path)).layout.entry)
    assert (lay.gate_ctrl, lay.gate_mask) == (lay.ctrl, lay.ctrl_mask)
    silence = image.find_code(CodePattern("C0 FE D0 09 BD"), start=lay.entry).addr
    duration = image.find_code(CodePattern("A9 FE 9D ?? ?? 20"), start=silence).addr
    for hi in (silence + 6, silence + 11, duration + 4):
        image.poke(hi, 0xA0)
    moved = layout.discover(image, lay.entry)
    assert (moved.gate_ctrl, moved.gate_mask) == (
        (lay.ctrl & 0xFF) | 0xA000,
        (lay.ctrl_mask & 0xFF) | 0xA000,
    )
    assert (moved.ctrl, moved.ctrl_mask) == (lay.ctrl, lay.ctrl_mask)


def test_orderlist_step_is_read_from_the_row_end_code(tune_path):
    """The end marker, restart index and jump store are the step's own operands."""
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(image, Player(reader.read(tune_path)).layout.entry)
    assert (lay.order_end, lay.order_restart) == (0xFF, 0)
    assert lay.order_jump == (lay.order_cursor if "3LUX" in tune_path.name else 0)
    end = image.find_code(CodePattern("C9 FF D0 02 A0 00"), start=lay.entry).addr
    image.poke(end + 1, 0x00)
    image.poke(end + 5, 0x04)
    if lay.order_jump:
        hook = image.find_code(CodePattern("0A 9D ?? ?? A8"), start=end).addr
        image.poke(hook + 3, image.peek(hook + 3) - 4)
    moved = layout.discover(image, lay.entry)
    assert (moved.order_end, moved.order_restart) == (0x00, 0x04)
    assert moved.order_jump == (lay.order_jump - 0x400 if lay.order_jump else 0)
    image.poke(end, 0xEA)
    assert layout.discover(image, lay.entry) == replace(lay, order_jump=0)


def test_inline_order_commands_store_where_the_code_does(later_tune_path):
    lay, _ = _layout(later_tune_path)
    assert lay.order_tempo == (lay.tempo_reload, lay.tempo)
    assert lay.order_jump == lay.order_cursor


def test_duration_stores_are_read_from_the_parse_routine(tune_path):
    image = SidImage.from_bytes(tune_path.read_bytes())
    lay = layout.discover(image, Player(reader.read(tune_path)).layout.entry)
    assert lay.dur_long == lay.dur_short == lay.dur_note == lay.dur
    long = image.find_code(CodePattern("C9 A0 90 ?? 29 1F 9D"), start=lay.entry).addr
    image.poke(long + 8, 0xA0)
    moved = layout.discover(image, lay.entry)
    assert moved.dur_long == (lay.dur & 0xFF) | 0xA000
    assert (moved.dur, moved.dur_short, moved.dur_note) == (lay.dur,) * 3
