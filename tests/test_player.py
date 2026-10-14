"""Player unit tests (structure, framing, determinism).

Byte-exact validation against the SID register oracle lives in
:mod:`tests.test_oracle_hvsc` (marker ``oracle``).  These tests exercise the
:class:`~pymusicassembler.player.Player` machinery inherited from
:class:`pysidtracker.MemPlayer` without needing Docker.
"""

import pytest

from pymusicassembler import constants, reader
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
FREE = 0xF000  # RAM no tested tune uses


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
    "lo,hi,expect",
    [
        (0x00, 0x01, (0x00, 0x01)),
        (0x00, 0x02, (0xF0, 0x01)),
        (0x20, 0x01, (0x10, 0x01)),
    ],
)
def test_arp_descent_below_page_one_is_undone(tune_path, lo, hi, expect):
    player = Player(reader.read(tune_path))
    lay = player.layout
    rec = FREE
    _poke(player, rec, (0, 0, 0, 0, 0, 0x01, 0x10, 0))
    _poke(player, lay.arp, (0,))
    player._wr(lay.arp_phase, 0)
    player._wr(lay.porta_ctr, 0)
    player._wr(lay.freq_lo_w, lo)
    player._wr(lay.freq_hi_w, hi)
    player._arp(0, rec)
    assert (player._rd(lay.freq_lo_w), player._rd(lay.freq_hi_w)) == expect


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
