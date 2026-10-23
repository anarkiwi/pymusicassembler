"""Strict xfails of the top ``pysidtracker.pathdiff`` fixes against the sidtrace oracle.

Each case in ``tests/fixtures/pathdiff.json`` is one representative subtune of a
corpus-wide fix (``docs/oracle-testing.md``), failing with whydiff's explanation
until the fix lands; the strict xfail then turns the run red until it is pruned.
"""

from pathlib import Path

import pytest

from pysidtracker.pathdiff import check_case, xfail_params

MANIFEST = Path(__file__).resolve().parent / "fixtures" / "pathdiff.json"

pytestmark = pytest.mark.oracle


@pytest.mark.parametrize("case", xfail_params(MANIFEST))
def test_pathdiff(case):
    check_case(case)
