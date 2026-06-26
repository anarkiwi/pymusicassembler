"""Byte-exact player validation against the SID register oracle.

The Music Assembler player renders from its first play call, which may be
one leading SILENT call (a duration counter underflows, the row is parsed,
no audible SID write is emitted) before the first audible call.  The oracle
write-stream cannot see that silent call, so its frame 0 is the first
audible call; the validator aligns by dropping up to a few leading silent
interpreter frames, exactly like deplayroutine's validator.
"""

import pytest

from pymusicassembler import reader
from pymusicassembler.player import Player, iter_frames, render_grid

NREG = 25
MAX_LEAD = 4


def _aligned_residual(oracle, rendered):
    """Return (ok, lead, divergence) aligning over leading silent frames."""
    if not rendered:
        return False, 0, (0, 0, -1, -1)
    baseline = rendered[0]
    best = None
    for lead in range(MAX_LEAD + 1):
        if lead and (lead > len(rendered) or rendered[lead - 1] != baseline):
            break
        div = None
        for frame in range(len(oracle)):
            row = rendered[lead + frame]
            for reg in range(NREG):
                if oracle[frame][reg] != row[reg]:
                    div = (frame, reg, oracle[frame][reg], row[reg])
                    break
            if div:
                break
        if div is None:
            return True, lead, None
        if best is None:
            best = div
    return False, 0, best


def test_byte_exact(tune_id, tune_path, oracle_grid):
    """The player reproduces the oracle's per-frame registers byte-exact."""
    oracle, source = oracle_grid
    song = reader.read(tune_path)
    rendered = render_grid(song, len(oracle) + MAX_LEAD)
    ok, lead, div = _aligned_residual(oracle, rendered)
    assert ok, (
        f"{tune_id}: not byte-exact (oracle {source}); "
        f"first divergence frame {div[0]} reg ${div[1]:02X} "
        f"expected ${div[2]:02X} got ${div[3]:02X}"
    )
    assert lead <= MAX_LEAD


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


def test_unknown_tune_skips_gracefully(tune_id):
    """The fixture machinery yields a valid id for every parametrization."""
    assert tune_id in ("space", "7years", "3lux")


@pytest.mark.parametrize("frames", [1, 50, 200])
def test_render_is_deterministic(tune_path, frames):
    """Two renders of the same song produce identical output."""
    song = reader.read(tune_path)
    assert render_grid(song, frames) == render_grid(song, frames)
