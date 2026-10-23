"""Player unit tests (structure, framing, determinism).

Byte-exact validation against the SID register oracle lives in
:mod:`tests.test_oracle_hvsc` (marker ``oracle``).  These tests exercise the
:class:`~pymusicassembler.player.Player` machinery inherited from
:class:`pysidtracker.MemPlayer` without needing Docker.
"""

from dataclasses import replace

import pytest

from pysidtracker import power_on_ram
from pysidtracker.asm6502 import assemble
from pymusicassembler import constants, reader
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


def _poke(player, addr, data):
    for i, byte in enumerate(data):
        player._wr(addr + i, byte)


def _orderlist(player, v, data):
    lay = player.layout
    _poke(player, FREE, data)
    player._wr(lay.order_lo + v, FREE & 0xFF)
    player._wr(lay.order_hi + v, FREE >> 8)
    _poke(player, FREE + 0x80, (constants.PATTERN_END,))
    player._wr(lay.repeat + v, 0)
    player._wr(lay.order_cursor + v, 0)
    player._row_end(v, FREE + 0x80, -1)


def test_orderlist_commands_of_a_later_build(later_tune_path):
    player = Player(reader.read(later_tune_path))
    lay = player.layout
    _orderlist(player, 0, (0x07, 0x21, 0xFC, 0x05, 0xFD, 0x00))
    assert player._rd(lay.tempo_reload) == player._rd(lay.tempo) == 5
    assert player._rd(lay.order_cursor) == 0
    assert player._rd(lay.pattern_id) == 0x07
    assert player._rd(lay.transrep) == 0x21 and player._rd(lay.repeat) == 1


def test_orderlist_without_tempo_command_reads_it_as_a_pattern(tune_path):
    player = Player(reader.read(tune_path))
    assert not player.layout.order_tempo
    _orderlist(player, 1, (0x07, 0x21, 0xFC, 0x05, constants.ORDER_END))
    assert player._rd(player.layout.pattern_id + 1) == 0xFC
    assert player._rd(player.layout.order_cursor + 1) == 2


def test_instrument_then_duration_gates_off(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    pid = player._rd(lay.pattern_id)
    pat = player._ptr(lay.pattern_lo, lay.pattern_hi, pid)
    _poke(player, pat, (0x81, 0x65, 0x00))
    player._wr(lay.pat_cursor, 0)
    player._wr(lay.ctrl, 0x41)
    player._parse(0)
    assert player._rd(lay.ctrl) == 0x40
    assert player._rd(lay.ctrl_mask) == 0xFE
    assert player._rd(lay.dur) == 5 and player._rd(lay.instr_x8) == 8
    assert player._rd(lay.pat_cursor) == 2


@pytest.mark.parametrize("row,cursor", [(1, 3), (0, 0), (5, 15), (6, 0)])
def test_program_jump_of_a_later_build(later_tune_path, row, cursor):
    player = Player(reader.read(later_tune_path))
    lay = player.layout
    _poke(player, FREE, (0x41, 0x80, 0, 0x21, 0x81, 0, 0xFD, row))
    player._wr(lay.inner_lo + 1, FREE & 0xFF)
    player._wr(lay.inner_hi + 1, FREE >> 8)
    player._wr(lay.instr_cursor, 3)
    player._wr(lay.ctrl_mask, 0xFF)
    player._program(0, 1)
    assert player._rd(lay.instr_cursor) == cursor
    assert player._rd(lay.ctrl) == 0x21
    assert player._rd(lay.freq_lo_w) == player._rd(lay.freq_lo + 1)


@pytest.mark.parametrize(
    "bound,row,cursor", [(0, 2, 6), (0, 4, 0), (5, 4, 12), (0xFE, 2, 0)]
)
def test_program_jump_bounds_by_its_own_cell(later_tune_path, bound, row, cursor):
    """A rip's unrelocated jump operand bounds the row by whatever cell it reads."""
    player = Player(reader.read(later_tune_path))
    lay = player.layout = player._lay = replace(player.layout, program_jump=FREE + 8)
    _poke(player, FREE, (0x41, 0x80, 0, 0xFD, row, 0, 0, 0, bound))
    player._wr(lay.inner_lo + 1, FREE & 0xFF)
    player._wr(lay.inner_hi + 1, FREE >> 8)
    player._wr(lay.instr_cursor, 0)
    player._wr(lay.ctrl_mask, 0xFF)
    player._program(0, 1)
    assert player._rd(lay.instr_cursor) == cursor


def _arp(player, lo, hi, ascend=0):
    """One arpeggio step of voice 0 from ``hi:lo`` with step $10 (instrument at FREE)."""
    lay = player._lay
    _poke(player, FREE, (0, 0, 0, 0, 0, 0x01, 0x10, 0))
    _poke(player, lay.arp, (ascend,))
    player._wr(lay.arp_phase, 0)
    player._wr(lay.porta_ctr, 0)
    player._wr(lay.freq_lo_w, lo)
    player._wr(lay.freq_hi_w, hi)
    player._arp(0, FREE - lay.instr)
    return player._rd(lay.freq_lo_w), player._rd(lay.freq_hi_w)


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
    player = Player(reader.read(tune_path))
    player.layout = player._lay = replace(player.layout, arp_down_cmp=cmp)
    assert _arp(player, 0x51, 0x07) == expect


def test_arp_ascent_reads_its_operand_from_the_code(tune_path):
    """The ascent adds to the cell its ``LDA abs,X`` names when it runs."""
    player = Player(reader.read(tune_path))
    lay = player.layout
    assert _arp(player, 0x20, 0x07, ascend=1) == (0x30, 0x07)
    _poke(player, lay.arp_up_src, (FREE & 0xFF, (FREE >> 8) + 1))
    player._wr(FREE + 0x100, 0x80)
    assert _arp(player, 0x20, 0x07, ascend=1) == (0x90, 0x07)


def test_odd_vibrato_step_alternates_with_the_plain_note(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout
    player._wr(lay.note_ctl, constants.CTL_VIBARP | constants.CTL_TRIGGERED)
    player._wr(lay.vib_lo_step, 0x03)
    player._wr(lay.vib_hi_step, 0x00)
    player._wr(lay.vib_toggle, 0xFF)
    player._wr(lay.vib_freq_lo, 0xFE)
    player._wr(lay.vib_freq_hi, 0x10)
    player._wr(lay.freq_lo_w, 0x55)
    player._wr(lay.freq_hi_w, 0x22)
    seen = []
    for _ in range(3):
        player._sid_final(0)
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
    player._wr(0xD403, 0x1F)
    player._wr(0xD40A, 0xF2)
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
        player.layout, sid=FREE + 0x200, res_sid=0xD500, fc_sid=0xD600
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
    player = Player(reader.read(tune_path))
    lay = player.layout = player._lay = replace(player.layout, pulse_always=always)
    rec = 0xC000
    player._wr(rec + constants.INSTR_PULSE_STEP, 0x20)
    player._wr(lay.pw_lo, 0x80)
    player._wr(lay.pw_hi, 0x08)
    player._pulse(0, rec, 0)
    assert player._rd(lay.pw_lo) | player._rd(lay.pw_hi) << 8 == expect


def test_vibrato_steps_the_output_cells_not_the_note_start(tune_path):
    player = Player(reader.read(tune_path))
    lay = player.layout = player._lay = replace(player.layout, vib_out_lo=0xC000)
    player._wr(lay.note_ctl, constants.CTL_VIBARP)
    player._wr(lay.vib_lo_step, 0x30)
    player._wr(lay.vib_hi_step, 0x00)
    player._wr(lay.vib_freq_lo, 0xEB)
    player._wr(lay.vib_out_lo, 0x8B)
    player._wr(lay.vib_out_hi, 0x15)
    player._sid_final(0)
    assert tuple(player.snapshot()[:2]) == (0xBB, 0x15)
    assert (player._rd(lay.vib_freq_lo), player._rd(lay.vib_out_lo)) == (0xEB, 0xBB)
