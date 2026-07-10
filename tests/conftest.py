"""Shared test fixtures: tune access and the byte-exact oracle.

Tunes are HVSC copyright works, never committed.  ``tune_path`` (built by the
shared :func:`pysidtracker.testing.make_tune_fixtures`) resolves a tune from a
local HVSC tree or the gitignored cache, fetching from the mirror on demand and
skipping the test when it is unavailable offline.  ``oracle_grid`` produces the
ground-truth per-frame SID register grid -- live from the
``preframr-sidtrace`` binary (``$SIDTRACE_BIN``) via the shared
:func:`pysidtracker.oracle.read_sidwr`/:func:`~pysidtracker.oracle.grid_from_writes`
framer when available, else from the committed frozen grid.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pysidtracker.oracle import grid_from_writes, read_sidwr
from pysidtracker.testing import make_tune_fixtures

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Parametrized ``tune_id`` / ``tune_path`` fixtures over the Music Assembler
# test tunes, resolving via the shared local-tree/cache/mirror logic.
tune_id, tune_path = make_tune_fixtures(fetch_tunes.TUNES, fetch_tunes.CACHE)


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
        return grid_from_writes(read_sidwr(prefix + ".sidwr.bin"))


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
def native_tune_path():
    """A tune whose image is a native self-start song (see fetch_tunes).

    Fetched on demand into the gitignored cache; skipped when unavailable
    offline, mirroring the shared ``tune_path`` fixture.
    """
    try:
        return fetch_tunes.fetch(fetch_tunes.NATIVE_TUNE)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"native tune unavailable: {exc}")


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
