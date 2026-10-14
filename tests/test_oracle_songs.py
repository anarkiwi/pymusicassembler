"""Whole-song byte-exact comparison against the oracle, one tune per player feature.

Marked ``oracle`` (Docker + network, see ``test_oracle_hvsc``).  Each tune is
rendered for at least its HVSC song length, so the comparison covers every
orderlist loop point rather than an opening window.
"""

import os
from pathlib import Path

import oracle_hvsc
import pytest

from pysidtracker import make_oracle_fixtures

_CACHE = Path(os.environ.get("PYMUSICASSEMBLER_ORACLE_CACHE", ".oracle-cache"))
_MAX_LEAD = 4

TUNES = {
    # Per-tune play-call divider reload (LDA #8 at the play entry's BMI target).
    "tempo_8": "MUSICIANS/G/Greatrix/VHD.sid",
}

# The longest HVSC song length of TUNES, in seconds.
SECONDS = 40


def _render(data, nframes):
    return oracle_hvsc.render(data, 1, nframes + _MAX_LEAD)[0].tolist()


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_CACHE / "hvsc",
    oracle_cache=_CACHE / "songs",
    render=_render,
    frames=None,
    seconds=SECONDS,
    max_lead=_MAX_LEAD,
)


@pytest.mark.oracle
def test_whole_song_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()
