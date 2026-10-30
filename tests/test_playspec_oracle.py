"""The playspec's memory against the tune's own 6502 over every oracle tune, whole song.

Marked ``oracle``: the tunes come from HVSC and the full-length runs are slow.
"""

import pytest

from pysidtracker.testing import make_tune_fixtures

from .test_oracle_hvsc import TUNES
from .test_playspec import assert_memory_equals_the_6502

tune_id, tune_path = make_tune_fixtures(TUNES)


@pytest.mark.oracle
def test_spec_memory_equals_the_6502_for_every_oracle_tune(tune_path):  # noqa: F811
    assert_memory_equals_the_6502(tune_path.read_bytes())
