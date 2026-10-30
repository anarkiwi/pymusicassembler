"""The tunes the oracle path differencing once marked strict xfails, held to the
tune's own 6502 over their whole songs.

Marked ``oracle``: the tunes come from HVSC and the full-length runs are slow.
"""

import pytest

from pysidtracker.testing import make_tune_fixtures

from .test_playspec import assert_memory_equals_the_6502

TUNES = {
    "mindbender": "MUSICIANS/T/Trident/Mindbender.sid",
    "vivian": "MUSICIANS/S/Siebold_Markus/Vivian_Co-op_Demo_tune_1.sid",
    "holiday_dream": "MUSICIANS/T/The_Blue_Ninja/Holiday_Dream_91_Endpart.sid",
    "project_argus": "MUSICIANS/W/Wells_Jon/Project_Argus_End.sid",
}

tune_id, tune_path = make_tune_fixtures(TUNES)


@pytest.mark.oracle
def test_spec_memory_equals_the_6502_for_every_pathdiff_tune(tune_path):  # noqa: F811
    assert_memory_equals_the_6502(tune_path.read_bytes())
