"""Shared test fixtures: tune access and the byte-exact oracle.

Tunes are HVSC copyright works, never committed.  ``tune_path`` fetches a
tune into a gitignored cache (skipping the test when the tune is absent and
there is no network), and ``oracle_grid`` produces the ground-truth
per-frame SID register grid -- live from the ``preframr-sidtrace`` binary
(``$SIDTRACE_BIN``) when available, else from the committed frozen grid --
mirroring deplayroutine's env-gated validator.
"""

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NREG = 25
PW_HI_REGS = {0x03, 0x0A, 0x11}
_REC = struct.Struct("<qHBB")
_PAL_CPF = 19656

# id -> committed frozen grid file (and the canonical tune id).
TUNE_IDS = ("space", "7years", "3lux")


def _try_fetch(tune_id):
    """Path to a cached tune, or None if it cannot be obtained."""
    rel = fetch_tunes.TUNES[tune_id]
    dest = fetch_tunes.CACHE / rel
    if dest.exists():
        return dest
    try:
        return fetch_tunes.fetch(rel)
    except Exception:  # pylint: disable=broad-except  # offline -> skip
        return None


@pytest.fixture(params=TUNE_IDS)
def tune_id(request):
    """Parametrize over each Music Assembler test tune id."""
    return request.param


@pytest.fixture
def tune_path(tune_id):
    """Path to the tune for ``tune_id``, skipping if unavailable."""
    path = _try_fetch(tune_id)
    if path is None:
        pytest.skip(f"tune {tune_id} unavailable (offline, not cached)")
    return path


def _read_sidwr(path):
    blob = Path(path).read_bytes()
    out = []
    for off in range(0, len(blob) - _REC.size + 1, _REC.size):
        cyc, _addr, reg, val = _REC.unpack_from(blob, off)
        if reg < NREG:
            out.append((cyc, reg, val))
    return out


def _first_play_cycle(writes, gap=10000):
    cyc = [w[0] for w in writes]
    for prev, cur in zip(cyc, cyc[1:]):
        if cur - prev > gap:
            return cur
    return cyc[0]


def _grid_from_writes(writes, cpf=_PAL_CPF):
    t0 = _first_play_cycle(writes)
    rows = []
    cur = [0] * NREG
    idx = 0
    while idx < len(writes) and writes[idx][0] < t0:
        _c, reg, val = writes[idx]
        cur[reg] = (val & 0x0F) if reg in PW_HI_REGS else val
        idx += 1
    nframes = (writes[-1][0] - t0 + cpf // 2) // cpf + 1
    for frame in range(nframes):
        while idx < len(writes) and (writes[idx][0] - t0 + cpf // 2) // cpf == frame:
            _c, reg, val = writes[idx]
            cur[reg] = (val & 0x0F) if reg in PW_HI_REGS else val
            idx += 1
        rows.append(cur[:])
    return rows


def _live_grid(tune_path, nframes=400):
    binary = os.environ.get("SIDTRACE_BIN")
    if not binary or not os.path.exists(binary):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "trace")
        subprocess.run(
            [binary, str(tune_path), "0", str(nframes), prefix],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _grid_from_writes(_read_sidwr(prefix + ".sidwr.bin"))


def _frozen_grid(tune_id):
    path = FIXTURES / f"{tune_id}.grid.txt"
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append([int(tok, 16) for tok in line.split()])
    return rows


@pytest.fixture
def oracle_grid(tune_id, tune_path):
    """Ground-truth grid: live sidtrace if available, else the frozen grid."""
    grid = _live_grid(tune_path)
    source = "live-sidtrace"
    if grid is None:
        grid = _frozen_grid(tune_id)
        source = "frozen-grid"
    if grid is None:
        pytest.skip(f"no oracle for {tune_id}")
    return grid, source
