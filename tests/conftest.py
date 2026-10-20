"""Shared test fixtures: HVSC tune access.

Tunes are HVSC copyright works, never committed.  ``tune_path`` (built by the
shared :func:`pysidtracker.testing.make_tune_fixtures`) resolves a tune from a
local ``$HVSC`` tree or the gitignored cache, fetching from the mirror on demand
and skipping the test when it is unavailable offline.  The byte-exact oracle
comparison lives in :mod:`tests.test_oracle_hvsc` (marker ``oracle``), which
reuses :func:`pysidtracker.testing.make_oracle_fixtures`.
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker import fixture as native_fixture
from pysidtracker import load_manifest
from pysidtracker.testing import make_tune_fixtures

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

# Editor native fixtures: SHA-256 pins in tests/fixtures/ma_native.txt, files cached gitignored by scripts/fetch_native.py.
NATIVE_MANIFEST = REPO / "tests" / "fixtures" / "ma_native.txt"
NATIVE_CACHE = Path(os.environ.get("MA_NATIVECACHE", str(REPO / "tests" / ".native")))

# Parametrized ``tune_id`` / ``tune_path`` fixtures over the Music Assembler
# test tunes, resolving via the shared local-tree/cache/mirror logic.
tune_id, tune_path = make_tune_fixtures(fetch_tunes.TUNES, fetch_tunes.CACHE)


def _fetch_or_skip(relpath):
    try:
        return fetch_tunes.fetch(relpath)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"tune unavailable: {exc}")


@pytest.fixture
def later_tune_path():
    """A later-build tune (see ``fetch_tunes.LATER_TUNE``)."""
    return _fetch_or_skip(fetch_tunes.LATER_TUNE)


@pytest.fixture
def native_tune_path():
    """A tune whose image is a native self-start song (see fetch_tunes).

    Fetched on demand into the gitignored cache; skipped when unavailable
    offline, mirroring the shared ``tune_path`` fixture.
    """
    return _fetch_or_skip(fetch_tunes.NATIVE_TUNE)


def _native(name: str) -> Path:
    """A committed-pin native fixture from the cache; skip when it is absent."""
    entry = {e.name: e for e in load_manifest(NATIVE_MANIFEST)}[name]
    path = native_fixture(entry.sha256, NATIVE_CACHE)
    if path is None:
        pytest.skip(f"native fixture {name!r} not cached; run scripts/fetch_native.py")
    return path


@pytest.fixture
def preset_bank_path():
    """The release ``P.`` preset bank (the editor's P-SAVE PRESET file)."""
    return _native("P.")


@pytest.fixture
def shape_song_path():
    """The release ``S.FOR SHAPE`` song on the same disk as ``P.``."""
    return _native("S.FOR SHAPE")


@pytest.fixture
def silence_tune_path():
    """A tune parked on the single-byte $FE silence terminator (#24)."""
    return _fetch_or_skip(fetch_tunes.SILENCE_TUNE)


@pytest.fixture
def jump_tune_path():
    """A tune whose orderlists end on a $FD n jump (#24)."""
    return _fetch_or_skip(fetch_tunes.JUMP_TUNE)
