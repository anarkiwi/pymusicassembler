"""Shared test fixtures: HVSC tune and editor native-file access.

Tunes are HVSC copyright works, never committed.  ``tune_path`` (built by the
shared :func:`pysidtracker.testing.make_tune_fixtures`) and ``tune`` resolve a
tune from a local ``$HVSC`` tree or the gitignored cache, fetching from the
mirror on demand and skipping the test when it is unavailable offline.  The
editor's ``P.``/``S.`` files are pinned by SHA-256 in
``tests/fixtures/ma_native.txt`` and cached by
``pysidtracker fixtures fetch tests/fixtures/ma_native.txt --cache tests/.native``.
The byte-exact oracle comparison lives in :mod:`tests.test_oracle_hvsc` (marker
``oracle``), which reuses :func:`pysidtracker.testing.make_oracle_fixtures`.
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker import fixture as native_fixture
from pysidtracker import load_manifest
from pysidtracker.testing import make_tune_fixtures, resolve_tune

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

TUNE_CACHE = Path(os.environ.get("MA_TUNECACHE", str(REPO / "tests" / ".tunecache")))
NATIVE_MANIFEST = REPO / "tests" / "fixtures" / "ma_native.txt"
NATIVE_CACHE = Path(os.environ.get("MA_NATIVECACHE", str(REPO / "tests" / ".native")))

# id -> HVSC relative path.  Space Invadarz on Vacation is the decompile
# reference; 7 Years and 3LUX Intro exercise per-tune table relocation and the
# self-modified filter envelope.
TUNES = {
    "space": "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "7years": "DEMOS/0-9/7_Years.sid",
    "3lux": "DEMOS/0-9/3LUX_Intro.sid",
}

tune_id, tune_path = make_tune_fixtures(TUNES, TUNE_CACHE)


@pytest.fixture
def tune():
    """``tune(relpath)``: the tune's local path; skips when it is unreachable."""

    def _tune(relpath):
        path = resolve_tune(relpath, cache_dir=TUNE_CACHE)
        if path is None:
            pytest.skip(f"{relpath} unavailable (offline, not cached)")
        return Path(path)

    return _tune


@pytest.fixture
def later_tune_path(tune):
    """A later build (init before play; orderlist $FC/$FD and wave-program $FD)."""
    return tune("MUSICIANS/C/Cooper/Song.sid")


@pytest.fixture
def native_tune_path(tune):
    """A native editor song wrapped in a PSID: its image carries the self-start
    stub verbatim (``78 20 48 50 ...`` at load $5000)."""
    return tune("DEMOS/A-F/Barbara.sid")


@pytest.fixture
def silence_tune_path(tune):
    """A tune parked on the single-byte $FE silence terminator (#24)."""
    return tune("MUSICIANS/F/Feekzoid/Another_Feekzoid_Digi_Tune.sid")


@pytest.fixture
def jump_tune_path(tune):
    """A later build whose orderlists all end on a $FD n jump (#24)."""
    return tune("DEMOS/M-R/Raccoon_intro.sid")


def _native(name: str) -> Path:
    """A committed-pin native fixture from the cache; skip when it is absent."""
    entry = {e.name: e for e in load_manifest(NATIVE_MANIFEST)}[name]
    path = native_fixture(entry.sha256, NATIVE_CACHE)
    if path is None:
        pytest.skip(
            f"native fixture {name!r} not cached; run pysidtracker fixtures fetch "
            "tests/fixtures/ma_native.txt --cache tests/.native"
        )
    return path


@pytest.fixture
def preset_bank_path():
    """The release ``P.`` preset bank (the editor's P-SAVE PRESET file)."""
    return _native("P.")


@pytest.fixture
def shape_song_path():
    """The release ``S.FOR SHAPE`` song on the same disk as ``P.``."""
    return _native("S.FOR SHAPE")
