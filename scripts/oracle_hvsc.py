"""pymusicassembler's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import cadence_info

from pymusicassembler import Player, parse
from pymusicassembler.constants import DEFAULT_LOAD, NATIVE_PLAY_OFFSET

STANDARD_BASE = DEFAULT_LOAD - NATIVE_PLAY_OFFSET

PLAYER = r"^\(?music_assembler(/mc)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a Music Assembler ``.sid`` and the build it decoded."""
    tune = parse(data, song - 1)
    player = Player(tune, subtune=song - 1)
    info = {
        "load_offset": tune.load_addr - player.base,
        "relocated": player.base != STANDARD_BASE,
        "tempo": player.tempo_reload,
    }
    info.update(player.layout.as_info())
    info.update(cadence_info(data, song))
    return np.asarray(player.render_grid(nframes), np.int16), info
