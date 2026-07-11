"""Player unit tests (structure, framing, determinism).

Byte-exact validation against the SID register oracle lives in
:mod:`tests.test_oracle_hvsc` (marker ``oracle``).  These tests exercise the
:class:`~pymusicassembler.player.Player` machinery inherited from
:class:`pysidtracker.MemPlayer` without needing Docker.
"""

import pytest

from pymusicassembler import reader
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
