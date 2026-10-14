"""Whole-song byte-exact comparison against the oracle, one tune per player feature.

Marked ``oracle`` (Docker + network, see ``test_oracle_hvsc``).  Each tune is
rendered for at least its HVSC song length, so the comparison covers every
orderlist loop point rather than an opening window.
"""

import os
from pathlib import Path

import oracle_hvsc
import pytest

from pysidtracker import SidImage, make_oracle_fixtures

_CACHE = Path(os.environ.get("PYMUSICASSEMBLER_ORACLE_CACHE", ".oracle-cache"))
_MAX_LEAD = 4

TUNES = {
    # Per-tune play-call divider reload (LDA #8 at the play entry's BMI target).
    "tempo_8": "MUSICIANS/G/Greatrix/VHD.sid",
    # Later build: init before play, orderlist $FC/$FD and wave-program $FD.
    "later_build": "MUSICIANS/C/Cooper/Song.sid",
    # Orderlist $FD jump through the order-advance hook.
    "order_jump": "MUSICIANS/A/Anakonda/Nightmare_Slide_Preview.sid",
    # RES_FILT presets read from the player's own table.
    "res_presets": "DEMOS/A-F/Bit_Shit.sid",
    # Filter commands retarget the trigger-reset voice and its reset values.
    "filter_sweep": "DEMOS/A-F/After8ty_01.sid",
    # Default song 2: init selects the song from the accumulator.
    "start_song": "MUSICIANS/T/The_Blue_Ninja/Holiday_Dream_91_Endpart.sid",
    # One of several relocated player copies, placed by init.
    "relocated_copy": "MUSICIANS/H/Hultink_Gerard/Kids.sid",
    # Pulse-width highs written with their upper nibble set.
    "pw_hi_nibble": "MUSICIANS/R/Ramos/Variation.sid",
    # A record read past the image sees libsidplayfp's power-up RAM.
    "power_on_ram": "MUSICIANS/M/Merman/Triumph_original_mix.sid",
    # An orderlist that parks a voice on the silence pattern, unterminated.
    "silence_parked": "MUSICIANS/H/Harlequin/Evacuation.sid",
    # The play routine self-modifies the JSR into its song's player copy.
    "self_modified_call": "MUSICIANS/K/Kaiser_Cameron/Nether_preview.sid",
    # Two player instances each frame, the second on a second SID.
    "two_players": "MUSICIANS/J/JVD/Double_2SID.sid",
}

# At least the longest HVSC song length of TUNES, in seconds.
SECONDS = 101


def _render(data, nframes):
    song = SidImage.from_bytes(data).header.start_song
    return oracle_hvsc.render(data, song, nframes + _MAX_LEAD)[0].tolist()


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
