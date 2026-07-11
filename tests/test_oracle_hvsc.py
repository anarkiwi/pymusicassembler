"""Byte-exact comparison of the Music Assembler player against the oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``);
a dedicated CI job runs ``pytest -m oracle``.  They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression.  HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

The tunes below cover every Music Assembler player variation this package
supports, each confirmed byte-exact against the deterministic sidtrace oracle:

* Standard ``$1021`` PSID -- player base ``$1000``, image loads at ``base+$21``
  (the PSID JMP vectors precede the play routine): space / 7years / 3lux.
* Image loads at the player base (``load == base``, no ``$21`` vector offset):
  acid_mix (base/load ``$1000``).
* Relocated player (base != ``$1000``) -- exercises the base-relative work-RAM
  and table relocation: corvus (``$2e00``), dr_doom (``$1200``), kasza_nota
  (``$1500``).
* Native self-start editor song wrapped in a PSID (a ``$78`` SEI stub at the
  image start in place of PSID vectors): eastern_promise (``$1b00``).

All are single-speed (one play call per frame); the player renders one leading
silent play call before the first audible frame, which ``make_oracle_fixtures``
aligns over via ``max_lead``.
"""

import os
from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures

from pymusicassembler.player import Player
from pymusicassembler.reader import read

# Cache under the workspace (a Docker-daemon-visible path, and what CI persists
# via actions/cache). ``$PYMUSICASSEMBLER_ORACLE_CACHE`` overrides the location.
_CACHE = Path(os.environ.get("PYMUSICASSEMBLER_ORACLE_CACHE", ".oracle-cache"))

# The player renders one leading silent play call the oracle write-stream cannot
# see, so render a few extra frames for make_oracle_fixtures to align over.
_MAX_LEAD = 4

TUNES = {
    # Standard $1021 PSID (player base $1000, load = base+$21).
    "space": "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "7years": "DEMOS/0-9/7_Years.sid",
    "3lux": "DEMOS/0-9/3LUX_Intro.sid",
    # Image loads at the player base (load == base, no $21 vector offset).
    "acid_mix": "DEMOS/A-F/Acid_Mix.sid",
    # Relocated player (base != $1000): work-RAM / table relocation.
    "corvus": "MUSICIANS/G/Ghetto_boy/Corvus.sid",
    "dr_doom": "DEMOS/A-F/Dr_Doom_01.sid",
    "kasza_nota": "MUSICIANS/A/ASL/Kasza-Nota.sid",
    # Native self-start editor song wrapped in a PSID ($78 stub at image start).
    "eastern_promise": "MUSICIANS/M/Merman/Eastern_Promise.sid",
}


def _render(data, nframes):
    return Player(read(data)).render_grid(nframes + _MAX_LEAD)


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_CACHE / "hvsc",
    oracle_cache=_CACHE / "csv",
    render=_render,
    frames=250,
    max_lead=_MAX_LEAD,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()
