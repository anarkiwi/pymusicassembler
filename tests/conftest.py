"""Shared test fixtures: HVSC tune access.

Tunes are HVSC copyright works, never committed.  ``tune_path`` (built by the
shared :func:`pysidtracker.testing.make_tune_fixtures`) resolves a tune from a
local ``$HVSC`` tree or the gitignored cache, fetching from the mirror on demand
and skipping the test when it is unavailable offline.  The byte-exact oracle
comparison lives in :mod:`tests.test_oracle_hvsc` (marker ``oracle``), which
reuses :func:`pysidtracker.testing.make_oracle_fixtures`.
"""

import sys
from pathlib import Path

import pytest

from pysidtracker.testing import make_tune_fixtures

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

# Parametrized ``tune_id`` / ``tune_path`` fixtures over the Music Assembler
# test tunes, resolving via the shared local-tree/cache/mirror logic.
tune_id, tune_path = make_tune_fixtures(fetch_tunes.TUNES, fetch_tunes.CACHE)


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
