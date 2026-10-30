"""Player unit tests (structure, framing, determinism).

Byte-exact validation against the SID register oracle lives in
:mod:`tests.test_oracle_hvsc` (marker ``oracle``).  These tests exercise the
:class:`~pymusicassembler.player.Player` machinery inherited from
:class:`pysidtracker.MemPlayer` without needing Docker.
"""

import pytest

from pysidtracker import CodePattern, SidImage, power_on_ram
from pysidtracker.asm6502 import assemble
from pymusicassembler import constants, playspec, reader
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.player import Player, iter_frames, render_grid

NREG = 25


def test_player_is_memplayer_subclass():
    """The one tracker class derives from the shared MemPlayer base."""
    import pysidtracker

    assert issubclass(Player, pysidtracker.MemPlayer)


def test_first_frame_all_registers(tune_path):
    """The first play_frame reports all 25 registers."""
    song = reader.read(tune_path)
    writes = Player(song).play_frame()
    assert [reg for reg, _ in writes] == list(range(NREG))


def test_iter_frames_changed_only(tune_path):
    """After the first frame only changed registers are reported."""
    song = reader.read(tune_path)
    frames = list(iter_frames(song, max_frames=8))
    assert len(frames) == 8
    assert len(frames[0]) == NREG
    # Some later frame reports fewer than all registers (sparse updates).
    assert any(len(frame) < NREG for frame in frames[1:])


def test_max_frames_required_for_termination(tune_path):
    """The player loops forever; iter_frames stops only at max_frames."""
    song = reader.read(tune_path)
    assert len(list(iter_frames(song, max_frames=1000))) == 1000


def test_render_grid_shape(tune_path):
    """render_grid yields the requested number of 25-register rows."""
    song = reader.read(tune_path)
    rows = render_grid(song, 12)
    assert len(rows) == 12
    assert all(len(row) == NREG for row in rows)


def test_render_grid_is_forward_filled(tune_path):
    """Each render_grid row is the full forward-filled register file."""
    song = reader.read(tune_path)
    rows = render_grid(song, 20)
    # Volume/filter are set at init and never cleared, so they persist.
    assert all(row[0x18] for row in rows)  # MODE_VOL stays nonzero


def test_unknown_tune_skips_gracefully(tune_id):
    """The fixture machinery yields a valid id for every parametrization."""
    assert tune_id in ("space", "7years", "3lux")


@pytest.mark.parametrize("frames", [1, 50, 200])
def test_render_is_deterministic(tune_path, frames):
    """Two renders of the same song produce identical output."""
    song = reader.read(tune_path)
    assert render_grid(song, frames) == render_grid(song, frames)


@pytest.mark.parametrize("reload", [0, 1, 4])
def test_rows_advance_every_reload_plus_one_calls(tune_path, reload):
    """The play entry reloads the divider from the image's ``LDA #n``."""
    song = reader.read(tune_path)
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0xA2, 0x00, 0xCE))) + 7 + 0x0C
    image[at + 1] = reload
    song.image = bytes(image)
    player = Player(song)
    assert player.tempo_reload == reload
    underflows = []
    for frame in range(12):
        player.play_frame()
        if (
            player._rd(player.layout.tempo) == reload
        ):  # pylint: disable=protected-access
            underflows.append(frame)
    assert underflows == list(range(0, 12, reload + 1))


# pylint: disable=protected-access
FREE = 0xC000  # RAM no tested tune uses
RECORD = 0xF0  # an instrument offset whose record the tests lay out themselves


def rd(player, addr):
    return player._mem[addr]


def wr(player, addr, value):
    player._mem[addr] = value & 0xFF


def poke(player, addr, data):
    for i, byte in enumerate(data):
        wr(player, addr + i, byte)


def variant(song, **cells):
    """A player of ``song`` whose spec layout has ``cells`` replaced."""

    def bind(view, entry):
        return {**playspec.binding(view, entry), **cells}

    return Player(song, bind=bind)


def rows(player, v):
    """One play call in which voice ``v`` reads its next row and the others sound."""
    lay = player.layout
    for w in range(constants.VOICES):
        wr(player, lay.dur + w, 0 if w == v else 0x7F)
    wr(player, lay.tempo, 0)
    player.play_frame()


def sound(player, v=0, ctl=0, flags=0, x8=0, sweep_ctr=0):
    """One play call between rows, every voice past its trigger: voice ``v`` with
    note control ``ctl``, ``flags`` and instrument offset ``x8``, the rest idle; the
    filter sweep runs ``sweep_ctr`` frames."""
    lay = player.layout
    wr(player, lay.sweep_ctr, sweep_ctr)
    for w in range(constants.VOICES):
        mine = w == v
        wr(player, lay.note_ctl + w, (ctl if mine else 0) | constants.CTL_TRIGGERED)
        wr(player, lay.flags + w, flags if mine else 0)
        wr(player, lay.instr_x8 + w, x8 if mine else 0)
    wr(player, lay.tempo, 5)
    player.play_frame()


def _pattern(player, data, v=0):
    """Point voice ``v`` at ``data`` as its current pattern, cursor at row 0."""
    lay = player.layout
    poke(player, FREE + 0x40, data)
    pid = rd(player, lay.pattern_id + v)
    wr(player, lay.pattern_lo + pid, (FREE + 0x40) & 0xFF)
    wr(player, lay.pattern_hi + pid, (FREE + 0x40) >> 8)
    wr(player, lay.pat_cursor + v, 0)
    return FREE + 0x40


def _step(player, v):
    """Voice ``v``'s pattern ends, so the orderlist step runs."""
    lay = player.layout
    poke(player, FREE + 0x80, (0x60, constants.PATTERN_END))
    wr(player, lay.pattern_id + v, 0)
    wr(player, lay.pattern_lo, (FREE + 0x80) & 0xFF)
    wr(player, lay.pattern_hi, (FREE + 0x80) >> 8)
    wr(player, lay.pat_cursor + v, 0)
    rows(player, v)


def _orderlist(player, v, data):
    lay = player.layout
    poke(player, FREE, data)
    wr(player, lay.order_lo + v, FREE & 0xFF)
    wr(player, lay.order_hi + v, FREE >> 8)
    wr(player, lay.repeat + v, 0)
    wr(player, lay.order_cursor + v, 0)
    _step(player, v)


def _program(player, data, slot=1, **state):
    """Voice 0 runs program ``slot`` (at ``FREE``, rows ``data``) in one play call."""
    lay = player.layout
    poke(player, FREE, data)
    wr(player, lay.inner_lo + slot, FREE & 0xFF)
    wr(player, lay.inner_hi + slot, FREE >> 8)
    wr(player, lay.ctrl_mask, 0xFF)
    for name, value in state.items():
        wr(player, getattr(lay, name), value)
    sound(player, flags=slot)


def test_orderlist_commands_of_a_later_build(later_tune_path):
    player = Player(reader.read(later_tune_path))
    lay = player.layout
    _orderlist(player, 0, (0x07, 0x21, 0xFC, 0x05, 0xFD, 0x00))
    assert rd(player, lay.tempo_reload) == rd(player, lay.tempo) == 5
    assert rd(player, lay.order_cursor) == 0
    assert rd(player, lay.pattern_id) == 0x07
    assert rd(player, lay.transrep) == 0x21 and rd(player, lay.repeat) == 1


def _native_order_hook(song):
    """``song`` whose orderlist step jumps to a routine of its own that marks the
    ``$FF`` it is handed in ``$C100`` and returns to the code after the compare."""
    image = SidImage.from_prg(song.load_addr.to_bytes(2, "little") + bytes(song.image))
    found = Player(song).layout
    if found.variant.entries["order_step"].value != "plain":
        pytest.skip("this build's step calls the order hook")
    entry = found.entry
    step = image.find_code(CodePattern("C8 B1 ?? C9 ?? D0 02 A0 ??"), start=entry).addr
    hook = song.load_addr + len(song.image)
    code, _ = assemble(
        f"""
  CMP #$FF
  BNE back
  LDA #$5A
  STA $C100
back: JMP ${step + 9:04X}
""",
        hook,
    )
    patched = bytearray(song.image) + bytes(code)
    for i, byte in enumerate((0x4C, hook & 0xFF, hook >> 8, 0xEA)):
        patched[step + 3 + i - song.load_addr] = byte
    song.image = bytes(patched)
    return song


def test_a_rips_own_order_hook_runs_on_the_cpu(tune_path):
    """The step hands the rip's routine the byte it read and carries on where it
    returns, so an end marker it does not restart on is played as the pattern id."""
    player = Player(_native_order_hook(reader.read(tune_path)))
    lay = player.layout
    assert lay.order_native
    _orderlist(player, 0, (0x07, 0x21, 0xFF, 0x00))
    assert rd(player, 0xC100) == 0x5A
    assert rd(player, lay.pattern_id) == 0xFF
    assert rd(player, lay.order_cursor) == 2


def test_orderlist_without_tempo_command_reads_it_as_a_pattern(tune_path):
    player = Player(reader.read(tune_path))
    assert not player.layout.order_tempo
    _orderlist(player, 1, (0x07, 0x21, 0xFC, 0x05, constants.ORDER_END))
    assert rd(player, player.layout.pattern_id + 1) == 0xFC
    assert rd(player, player.layout.order_cursor + 1) == 2


def test_instrument_then_duration_gates_off(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (0x81, 0x65, 0x00))
    wr(player, lay.ctrl, 0x41)
    rows(player, 0)
    assert rd(player, lay.ctrl) == 0x40
    assert rd(player, lay.ctrl_mask) == 0xFE
    assert rd(player, lay.dur) == 5 and rd(player, lay.instr_x8) == 8
    assert rd(player, lay.pat_cursor) == 2


@pytest.mark.parametrize("row,cursor", [(1, 3), (0, 0), (5, 15), (6, 0)])
def test_program_jump_of_a_later_build(later_tune_path, row, cursor):
    player = Player(reader.read(later_tune_path))
    lay = player.layout
    _program(
        player,
        (0x41, 0x80, 0, 0x21, 0x81, 0, 0xFD, row),
        instr_cursor=3,
    )
    assert rd(player, lay.instr_cursor) == cursor
    assert rd(player, lay.ctrl) == 0x21
    assert rd(player, lay.freq_lo_w) == rd(player, lay.freq_lo + 1)


@pytest.mark.parametrize(
    "bound,row,cursor", [(0, 2, 6), (0, 4, 0), (5, 4, 12), (0xFE, 2, 0)]
)
def test_program_jump_bounds_by_its_own_cell(later_tune_path, bound, row, cursor):
    """A rip's unrelocated jump operand bounds the row by whatever cell it reads."""
    player = variant(reader.read(later_tune_path), program_jump=FREE + 8)
    lay = player.layout
    _program(player, (0x41, 0x80, 0, 0xFD, row, 0, 0, 0, bound), instr_cursor=0)
    assert rd(player, lay.instr_cursor) == cursor


def _arp(player, lo, hi, ascend=0):
    """One arpeggio step of voice 0 from ``hi:lo`` with step $10."""
    lay = player.layout
    poke(player, lay.instr + RECORD, (0, 0, 0, 0, 0, 0x01, 0x10, 0))
    poke(player, lay.arp, (ascend,))
    wr(player, lay.arp_phase, 0)
    wr(player, lay.porta_ctr, 0)
    wr(player, lay.freq_lo_w, lo)
    wr(player, lay.freq_hi_w, hi)
    sound(player, flags=0x10, x8=RECORD)
    return rd(player, lay.freq_lo_w), rd(player, lay.freq_hi_w)


@pytest.mark.parametrize(
    "lo,hi,expect",
    [
        (0x00, 0x01, (0x00, 0x01)),
        (0x00, 0x02, (0xF0, 0x01)),
        (0x20, 0x01, (0x10, 0x01)),
    ],
)
def test_arp_descent_below_page_one_is_undone(tune_path, lo, hi, expect):
    assert _arp(Player(reader.read(tune_path)), lo, hi) == expect


@pytest.mark.parametrize("cmp,expect", [(False, (0x41, 0x07)), (True, (0x51, 0x06))])
def test_arp_descent_compare_leaves_the_low_byte(tune_path, cmp, expect):
    """A build whose descent ``CMP``s instead of ``STA``s only borrows from the high byte."""
    player = variant(reader.read(tune_path), arp_down_cmp=cmp)
    assert _arp(player, 0x51, 0x07) == expect


def test_arp_ascent_reads_its_operand_from_the_code(tune_path):
    """The ascent adds to the cell its ``LDA abs,X`` names when it runs."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    assert _arp(player, 0x20, 0x07, ascend=1) == (0x30, 0x07)
    poke(player, lay.arp_up_src, (FREE & 0xFF, (FREE >> 8) + 1))
    wr(player, FREE + 0x100, 0x80)
    assert _arp(player, 0x20, 0x07, ascend=1) == (0x90, 0x07)


def test_odd_vibrato_step_alternates_with_the_plain_note(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    wr(player, lay.vib_lo_step, 0x03)
    wr(player, lay.vib_hi_step, 0x00)
    wr(player, lay.vib_toggle, 0xFF)
    wr(player, lay.vib_freq_lo, 0xFE)
    wr(player, lay.vib_freq_hi, 0x10)
    wr(player, lay.freq_lo_w, 0x55)
    wr(player, lay.freq_hi_w, 0x22)
    seen = []
    for _ in range(3):
        sound(player, ctl=constants.CTL_VIBARP)
        seen.append(tuple(player.snapshot()[:2]))
    assert seen == [(0x01, 0x11), (0x55, 0x22), (0x04, 0x11)]


def test_play_wrapper_runs_on_the_cpu(tune_path):
    song = reader.read(tune_path)
    direct = Player(song).render_grid(60)
    wrapper = song.load_addr + len(song.image)
    entry = Player(song).layout.entry
    song.image = bytes(song.image) + bytes(
        (0xA9, 0x1A, 0x8D, 0x18, 0xD4, 0x20, entry & 0xFF, entry >> 8, 0x60)
    )
    song.play_addr = wrapper
    wrapped = Player(song).render_grid(60)
    assert [row[:24] for row in wrapped] == [row[:24] for row in direct]
    assert {row[24] for row in wrapped[1:]} == {0x1A}


def test_power_on_ram_is_libsidplayfps():
    ram = power_on_ram()
    assert set(ram[:0x3FF]) == {0}
    assert ram[0x0400:0x0408] == bytes((0, 0, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0))
    assert ram[0x4000:0x4008] == bytes((0xFF, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF))
    assert ram[0x8000:0x8008] == ram[0x0400:0x0408]
    assert ram[0xFFF8:] == ram[0x4000:0x4008]


def test_unloaded_ram_reads_the_power_on_pattern(tune_path):
    song = reader.read(tune_path)
    player = Player(song)
    end = song.load_addr + len(song.image)
    assert (
        player.memory[end + 0x400 : end + 0x600]
        == power_on_ram()[end + 0x400 : end + 0x600]
    )


def test_snapshot_keeps_the_pulse_width_nibble(tune_path):
    player = Player(reader.read(tune_path))
    wr(player, 0xD403, 0x1F)
    wr(player, 0xD40A, 0xF2)
    regs = player.snapshot()
    assert regs[3] == 0x0F and regs[10] == 0x02


def _wrapped(song, code):
    """``song`` with ``code(wrapper_address)`` appended as its play routine."""
    wrapper = song.load_addr + len(song.image)
    song.image = bytes(song.image) + bytes(code(wrapper))
    song.play_addr = wrapper
    return song


def _twin(song):
    """``song`` with a second play entry, at ``$F021``, into the same player."""
    entry = Player(song).layout.entry
    image = bytearray(song.image)
    at = entry - song.load_addr
    body = bytes(image[at : at + 0x1B])
    song.image = bytes(image) + bytes(0xF021 - song.load_addr - len(image)) + body
    return song, entry


def test_self_modified_play_call_is_probed(tune_path):
    song, first = _twin(reader.read(tune_path))
    song = _wrapped(
        song,
        lambda w: (
            0xA9,
            0xF0,
            0x8D,
            (w + 7) & 0xFF,
            (w + 7) >> 8,
            0x20,
            0x21,
            0x00,
            0x60,
        ),
    )
    player = Player(song)
    assert player.layout.entry == 0xF021 != first
    player.render_grid(4)
    assert player.memory[song.play_addr + 7] == 0xF0


def test_every_player_instance_called_is_modelled(tune_path):
    song, first = _twin(reader.read(tune_path))
    song = _wrapped(
        song, lambda w: (0x20, first & 0xFF, first >> 8, 0x20, 0x21, 0xF0, 0x60)
    )
    player = Player(song)
    player.render_grid(4)
    assert sorted(entry for _, entry in player._layouts) == [first, 0xF021]


def test_player_under_a_banked_out_rom_is_modelled(tune_path):
    song = reader.read(tune_path)
    direct = Player(song).render_grid(60)
    entry = Player(song).layout.entry
    image = bytearray(song.image)
    at = entry - song.load_addr
    body = bytes(image[at : at + 0x1B])
    wrapper = bytes(
        (0xA9, 0x35, 0x85, 0x01, 0x20, 0x21, 0xE0, 0xA9, 0x37, 0x85, 0x01, 0x60)
    )
    image += bytes(FREE - song.load_addr - len(image)) + wrapper
    song.image = bytes(image + bytes(0xE021 - song.load_addr - len(image)) + body)
    song.play_addr = FREE
    player = Player(song)
    assert player.layout.entry == 0xE021
    assert [row[:24] for row in player.render_grid(60)] == [row[:24] for row in direct]
    assert player.memory[0xE021 : 0xE021 + len(body)] == body


@pytest.mark.parametrize(
    "magic, play, bits", [(b"PSID", None, 7), (b"RSID", None, 6), (b"PSID", 0, 6)]
)
def test_play_banks_the_cpu_port_as_psiddrv(tune_path, magic, play, bits):
    """psiddrv banks ``$01`` for play only for a PSID with a play address."""
    song = reader.read(tune_path)
    init = song.init_addr
    song = _wrapped(
        song, lambda w: (0x20, init & 0xFF, init >> 8, 0xA9, 0x36, 0x85, 0x01, 0x60)
    )
    song.init_addr, song.play_addr = song.play_addr, Player(song).layout.entry
    song.container_header = magic + bytes(song.container_header[4:])
    if play is not None:
        song.play_addr = play
    assert Player(song)._bus.bits == bits


def test_sweep_follows_its_own_operands(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    owner = FREE + 0x100
    player._wr(owner, 1)
    player._wr(lay.filter_owner, 0)
    player._lay = replace(lay, sweep_owner=owner)
    player._wr(lay.sweep_ctr, 2)
    player._wr(lay.sweep_acc, 0x40)
    player._wr(lay.sweep_step, 0x10)
    player._wr(lay.note_ctl, constants.CTL_TRIGGERED)
    player._wr(lay.note_ctl + 1, constants.CTL_TRIGGERED)
    player._sustain(0, lay.instr, constants.CTL_TRIGGERED)
    assert player._rd(lay.sweep_ctr) == 2
    player._sustain(1, lay.instr, constants.CTL_TRIGGERED)
    assert player._rd(lay.sweep_dec) == 1
    assert player._rd(lay.sweep_sta) == player.snapshot()[22] == 0x50


def test_registers_are_written_to_the_players_sid(tune_path):
    player = Player(reader.read(tune_path))
    player._lay = replace(player.layout, sid=0xD500)
    player._wr(0xD404, 0x00)
    player._set(constants.CTRL_REG, 0x41)
    assert player._rd(0xD504) == 0x41 and player.snapshot()[4] == 0x00


def test_filter_is_written_to_its_own_sid_page(tune_path):
    player = Player(reader.read(tune_path))
    lay = player._lay = replace(
        player.layout, sid=FREE + 0x200, res_sta=0xD517, fc_sid=0xD600
    )
    pat = FREE
    shadow = [player._rd(FREE + 0x215 + r) for r in range(4)]
    player._wr(pat + 1, constants.CTL_FILTER)
    player._wr(pat + 3, 0)
    player._note(0, pat, 0, 0)
    player._wr(lay.sweep_owner, 0)
    player._wr(lay.sweep_ctr, 1)
    player._wr(lay.sweep_acc, 0x40)
    player._wr(lay.sweep_step, 0x10)
    player._sustain(0, lay.instr, constants.CTL_TRIGGERED)
    assert (player._rd(0xD517), player._rd(0xD616)) == (0xF0, 0x50)
    assert [player._rd(FREE + 0x215 + r) for r in range(4)] == shadow


def _rsid(song, irq="CLI"):
    """``song`` as an RSID (play 0) whose init installs a KERNAL IRQ calling play."""
    init, entry = song.init_addr, Player(song).layout.entry
    org = song.load_addr + len(song.image)
    code, _ = assemble(
        f"""
  JSR ${init:04X}
  SEI
  LDA #<irq
  STA $0314
  LDA #>irq
  STA $0315
  {irq}
  RTS
irq: JSR ${entry:04X}
  JMP $EA31
""",
        org,
    )
    header = bytearray(song.container_header)
    header[:4] = b"RSID"
    header[0x0A:0x0E] = bytes((org >> 8, org & 0xFF, 0, 0))
    song.container_header = bytes(header)
    song.image = bytes(song.image) + code
    song.init_addr, song.play_addr = org, 0
    return song


def _changes(grid):
    return [row for i, row in enumerate(grid) if not i or row != grid[i - 1]]


def test_rsid_plays_on_its_installed_interrupt(tune_path):
    direct = render_grid(reader.read(tune_path), 40)
    player = Player(_rsid(reader.read(tune_path)))
    assert _changes(player.render_grid(40)) == _changes(direct)
    assert player._hosted.host.framed[0] == "cia_irq"


def test_rsid_frames_continue_after_a_grid(tune_path):
    song = _rsid(reader.read(tune_path))
    grid = Player(song).render_grid(12)
    player = Player(song)
    player.render_grid(6)
    assert [player.play_frame() is not None for _ in range(6)] == [True] * 6
    assert player.snapshot() == grid[-1]


def test_rsid_without_an_interrupt_is_a_parse_error(tune_path):
    player = Player(_rsid(reader.read(tune_path), irq="LDA #$7F\n  STA $DC0D"))
    with pytest.raises(MusicAssemblerParseError, match="no interrupt"):
        player.render_grid(4)


@pytest.mark.parametrize("always,expect", [(False, 0x0880), (True, 0x28A0)])
def test_pulse_always_adds_the_step(tune_path, always, expect):
    player = variant(reader.read(tune_path), pulse_always=always)
    lay = player.layout
    wr(player, lay.instr + RECORD + constants.INSTR_PULSE_STEP, 0x20)
    wr(player, lay.pw_lo, 0x80)
    wr(player, lay.pw_hi, 0x08)
    sound(player, x8=RECORD)
    assert rd(player, lay.pw_lo) | rd(player, lay.pw_hi) << 8 == expect


@pytest.mark.parametrize("limit,toggles", [(0x03, True), (0x04, False)])
def test_pulse_sweep_compares_against_its_own_cell(tune_path, limit, toggles):
    """A rip's unrelocated compare operand names the cell the substep is checked
    against, not the cell it counts in."""
    player = variant(reader.read(tune_path), pw_substep_cmp=FREE)
    lay = player.layout
    wr(player, lay.instr + RECORD + constants.INSTR_PULSE_STEP, 0x03)
    wr(player, FREE, limit)
    wr(player, lay.pw_dir, 0)
    wr(player, lay.pw_substep, 0)
    sound(player, flags=0x20, x8=RECORD)
    assert (rd(player, lay.pw_substep), rd(player, lay.pw_dir)) == (
        (0, 1) if toggles else (1, 0)
    )


@pytest.mark.parametrize("pw_lo,toggles", [(0xF0, True), (0x10, False)])
def test_adc_limit_adds_the_sweep_carry(tune_path, pw_lo, toggles):
    """A build whose substep limit is ``ADC #$0F`` adds the carry the pulse-width
    add left: it is checked after rising past a page boundary only."""
    player = variant(reader.read(tune_path), pw_substep_cmp=FREE, pulse_cmp_adc=True)
    lay = player.layout
    wr(player, lay.instr + RECORD + constants.INSTR_PULSE_STEP, 0x20)
    wr(player, FREE, 0x30)
    wr(player, lay.pw_dir, 1)
    wr(player, lay.pw_substep, 0)
    wr(player, lay.pw_lo, pw_lo)
    sound(player, flags=0x20, x8=RECORD)
    assert rd(player, lay.pw_dir) == (0 if toggles else 1)


def test_vibrato_steps_the_output_cells_not_the_note_start(tune_path):
    player = variant(reader.read(tune_path), vib_out_lo=FREE)
    lay = player.layout
    wr(player, lay.vib_lo_step, 0x30)
    wr(player, lay.vib_hi_step, 0x00)
    wr(player, lay.vib_freq_lo, 0xEB)
    wr(player, FREE, 0x8B)
    wr(player, lay.vib_out_hi, 0x15)
    sound(player, ctl=constants.CTL_VIBARP)
    assert tuple(player.snapshot()[:2]) == (0xBB, 0x15)
    assert (rd(player, lay.vib_freq_lo), rd(player, FREE)) == (0xEB, 0xBB)


def test_duration_gates_the_parse_routines_own_cells(tune_path):
    """The parse routine's gate-off masks the cells its own operands name."""
    player = variant(reader.read(tune_path), gate_ctrl=FREE, gate_mask=FREE + 3)
    lay = player.layout
    _pattern(player, (0x62, constants.PATTERN_END))
    poke(player, FREE, (0x41,))
    wr(player, lay.ctrl, 0x41)
    wr(player, lay.ctrl_mask, 0xFF)
    rows(player, 0)
    assert (rd(player, FREE), rd(player, FREE + 3)) == (0x40, 0xFE)
    assert (rd(player, lay.ctrl), rd(player, lay.ctrl_mask)) == (0x41, 0xFF)
    assert rd(player, lay.dur) == 0x62 & constants.CTL_DUR_MASK


def test_orderlist_end_and_restart_are_the_layouts(tune_path):
    """The step restarts at the code's own index when it meets its own end marker."""
    player = variant(reader.read(tune_path), order_end=0x00, order_restart=4)
    lay = player.layout
    _orderlist(player, 2, (0x07, 0x21, 0xFF, 0x00, 0x05, 0x13))
    assert rd(player, lay.pattern_id + 2) == 0xFF
    _step(player, 2)
    assert rd(player, lay.order_cursor + 2) == 4
    assert rd(player, lay.pattern_id + 2) == 0x05 and rd(player, lay.repeat + 2) == 3


def test_order_commands_write_the_code_cells(later_tune_path):
    """``$FC t`` and ``$FD n`` store into the cells the step's code names."""
    player = variant(
        reader.read(later_tune_path), order_tempo=(FREE + 0x40,), order_jump=FREE + 0x50
    )
    lay = player.layout
    reload = rd(player, lay.tempo_reload)
    _orderlist(player, 1, (0x07, 0x21, 0xFC, 0x05, 0xFD, 0x04, 0x00, 0x00, 0x09, 0x10))
    assert rd(player, FREE + 0x40) == 5 and rd(player, lay.tempo_reload) == reload
    assert rd(player, FREE + 0x51) == 8 and rd(player, lay.order_cursor + 1) == 8
    assert rd(player, lay.pattern_id + 1) == 0x09


def test_long_duration_stores_where_the_parse_routine_does(tune_path):
    player = variant(reader.read(tune_path), dur_long=FREE)
    lay = player.layout
    _pattern(player, (0xA7, 0x00))
    rows(player, 0)
    assert (rd(player, FREE), rd(player, lay.dur)) == (7, 0xFF)


@pytest.mark.parametrize(
    "row", [(0x10, 0x02), (0x62,), (0xA2,), (0x81, 0xA2), (0x10, 0x22, 1, 0)]
)
def test_a_row_lasts_its_duration_plus_one_tempo_ticks(tune_path, row):
    """Every row kind counts ``byte & $1F`` down to below zero before the next row."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, row * 3 + (constants.PATTERN_END,))
    rows(player, 0)
    parsed = []
    for tick in range(9):
        before = rd(player, lay.dur)
        for w in (1, 2):
            wr(player, lay.dur + w, 0x7F)
        wr(player, lay.tempo, 0)
        player.play_frame()
        if rd(player, lay.dur) >= before:
            parsed.append(tick)
    assert parsed == [2, 5, 8]


@pytest.mark.parametrize("byte,gate", [(0x62, 0x40), (0xA2, 0x41), (0xFF, 0x41)])
def test_rest_gates_off_and_hold_keeps_the_note(tune_path, byte, gate):
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (byte, 0x10, 0x02, constants.PATTERN_END))
    wr(player, lay.ctrl, 0x41)
    wr(player, lay.freq_lo_w, 0x55)
    rows(player, 0)
    assert rd(player, lay.ctrl) == gate
    assert rd(player, lay.freq_lo_w) == 0x55
    assert rd(player, lay.dur) == byte & constants.CTL_DUR_MASK


def test_empty_pattern_plays_its_ff_as_a_hold_and_reads_on(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (constants.PATTERN_END, 0x10, 0x02, constants.PATTERN_END))
    rows(player, 0)
    assert rd(player, lay.dur) == 0x1F
    assert rd(player, lay.pat_cursor) == 1


def test_instrument_select_persists_for_later_notes(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (0x82, 0x10, 0x00, 0x11, 0x00, constants.PATTERN_END))
    rows(player, 0)
    rows(player, 0)
    assert rd(player, lay.instr_x8) == 2 << 3
    assert rd(player, lay.pat_cursor) == 0


@pytest.mark.parametrize(
    "flags,ctl,called",
    [
        (0x11, constants.CTL_VIBARP, "program"),
        (0x11, 0, "program"),
        (0x10, constants.CTL_VIBARP, None),
        (0x10, 0, "arp"),
    ],
)
def test_vibrato_bit_suppresses_the_sweep_not_the_program(
    tune_path, flags, ctl, called
):
    """Flags' low nibble runs program 1..15 (0: none); bit 5 only stops the flag-$10 sweep."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    poke(player, FREE, (0x41, 0x00, 0x00, 0x00))
    wr(player, lay.inner_lo + 1, FREE & 0xFF)
    wr(player, lay.inner_hi + 1, FREE >> 8)
    wr(player, lay.instr_cursor, 0)
    wr(player, lay.porta_ctr, 5)
    sound(player, ctl=ctl, flags=flags)
    ran = []
    if rd(player, lay.instr_cursor) != 0:
        ran.append("program")
    if rd(player, lay.porta_ctr) != 5:
        ran.append("arp")
    assert ran == ([called] if called else [])


@pytest.mark.parametrize("cutoff,expect", [(0x55, 0x55), (0x00, 0x33)])
def test_program_cutoff_sets_the_filter_sweep_accumulator(tune_path, cutoff, expect):
    player = Player(reader.read(tune_path))
    lay = player.layout
    fc_hi = player.snapshot()[0x16]
    _program(
        player,
        (0x41, 0x00, cutoff, constants.PROGRAM_RESTART),
        instr_cursor=0,
        program_acc=0x33,
    )
    assert lay.program_acc == lay.sweep_acc
    assert rd(player, lay.sweep_acc) == expect
    assert player.snapshot()[0x16] == fc_hi


@pytest.mark.parametrize("length,ctr,res", [(0x07, 0x07, None), (0x00, 0x09, 0xF0)])
def test_filter_note_arms_the_trigger_sweep(tune_path, length, ctr, res):
    """The cutoff byte and its nibble's step 2n-16 load at the trigger; length 0 unroutes."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    _pattern(player, (0x10, 0x82, 0x4B, length, constants.PATTERN_END), v=1)
    wr(player, lay.fc_ctr_reset, 0x09)
    rows(player, 1)
    assert rd(player, lay.trigger_owner) == 1
    assert rd(player, lay.trigger_acc) == 0x4B
    assert rd(player, lay.sweep_step) == (2 * 0x0B - 0x10) & 0xFF
    assert rd(player, lay.trigger_ctr) == ctr
    expect = rd(player, lay.res_presets + 1) if res is None else res
    assert rd(player, lay.res_sta) == expect


def test_tempo_command_applies_before_the_next_entry_plays(later_tune_path):
    player = Player(reader.read(later_tune_path))
    lay = player.layout
    _orderlist(player, 0, (0x07, 0x00, 0xFC, 0x03, 0x08, 0x10, constants.ORDER_END))
    assert rd(player, lay.tempo_reload) == 3
    assert rd(player, lay.pattern_id) == 0x08
    assert rd(player, lay.order_cursor) == 4


def test_sweep_follows_its_own_operands(tune_path):
    """Only the voice the owner cell names steps the sweep, once a call."""
    player = variant(reader.read(tune_path), sweep_owner=FREE + 0x100)
    lay = player.layout
    wr(player, FREE + 0x100, 1)
    wr(player, lay.sweep_acc, 0x40)
    wr(player, lay.sweep_step, 0x10)
    sound(player, sweep_ctr=2)
    assert rd(player, lay.sweep_dec) == 1
    assert rd(player, lay.sweep_sta) == player.snapshot()[22] == 0x50


def test_registers_are_written_to_the_players_sid(tune_path):
    player = variant(reader.read(tune_path), sid=0xD500)
    wr(player, 0xD404, 0x00)
    wr(player, player.layout.ctrl, 0x41)
    sound(player)
    assert rd(player, 0xD504) == 0x41 and player.snapshot()[4] == 0x00


def test_filter_is_written_to_its_own_sid_page(tune_path):
    player = variant(
        reader.read(tune_path), sid=FREE + 0x200, res_sta=0xD517, fc_sid=0xD600
    )
    lay = player.layout
    shadow = [rd(player, FREE + 0x215 + r) for r in range(4)]
    _pattern(player, (0x00, constants.CTL_FILTER, 0x00, 0x00, constants.PATTERN_END))
    rows(player, 0)
    wr(player, lay.sweep_owner, 0)
    wr(player, lay.sweep_acc, 0x40)
    wr(player, lay.sweep_step, 0x10)
    sound(player, sweep_ctr=1)
    assert (rd(player, 0xD517), rd(player, 0xD616)) == (0xF0, 0x50)
    assert [rd(player, FREE + 0x215 + r) for r in range(4)] == shadow
