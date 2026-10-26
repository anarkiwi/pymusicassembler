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
* Alternative ``to_filter`` encoding (res-branch stored differently than the
  fixed-layout signature): chotaire / cleve (``$1000``).
* Orderlist transrep seeded into a separate work cell, not the transpose-read
  cell (which can alias the freq-hi table): anakonda / speed_warez.
* HVSC rips of release-disk editor ``S.`` files (``tests/test_editor_files.py``
  renders each editor file against its rip): parallax, untouched.
* Sustain program table in a C64 ROM bank, which the PSID driver's iomap
  banks the ROM-less set (0xEA kernal, zero basic) over: greece_ha,
  shifting_gear (``$5000``).
* Resonance stored to a SID mirror (``$D430``, aliasing voice 3's pulse-width
  low): crumb.

Each tune is rendered over the ``SECONDS`` window -- far deeper than an opening
window -- so the comparison spans multiple orderlist loop points (a regression
can strike late in a composition).  Tunes play one call per frame
(single-speed); the player renders one leading silent play call before the
first audible frame, which ``make_oracle_fixtures`` aligns over via ``max_lead``.
"""

import oracle_hvsc
import pytest

from pysidtracker import make_oracle_fixtures

# The player renders one leading silent play call the oracle write-stream cannot
# see, so render a few extra frames for make_oracle_fixtures to align over.
_MAX_LEAD = 4

# Oracle render window (seconds): deep into each (looping) composition so late-in-song differences are in the compared span.
SECONDS = 101

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
    # Alternative to_filter encoding (res branch stored differently).
    "chotaire": "MUSICIANS/C/Chotaire/It_Neednt_Be_a_Dream.sid",
    "cleve": "MUSICIANS/C/Cleve/Cleve_06.sid",
    # Orderlist transrep seeded into a separate work cell.
    "anakonda": "MUSICIANS/A/Anakonda/Anakonda.sid",
    "speed_warez": "MUSICIANS/A/Anakonda/Speed_Warez.sid",
    # Program table reading the C64 ROM banks (the driver's ROM-less iomap).
    "greece_ha": "MUSICIANS/D/Dr_Rox/Greece_Ha.sid",
    "shifting_gear": "MUSICIANS/M/MC/Shifting_Gear.sid",
    # Resonance stored to a SID mirror ($D430 aliases $D410).
    "crumb": "MUSICIANS/S/Spratt/Crumb.sid",
    # Rips of the editor S. files tests/test_editor_files.py renders against.
    "parallax": "MUSICIANS/M/MC/Parallax.sid",
    "untouched": "MUSICIANS/M/MC/Untouched.sid",
}


def _render(data, nframes):
    return oracle_hvsc.render(data, 1, nframes + _MAX_LEAD)[0].tolist()


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    render=_render,
    frames=None,
    seconds=SECONDS,
    max_lead=_MAX_LEAD,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()
