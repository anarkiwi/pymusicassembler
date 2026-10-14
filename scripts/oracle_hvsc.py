"""pymusicassembler's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import playroutine_cadence
from pysidtracker.registers import PAL_CYCLES_PER_FRAME

from pymusicassembler import Player, parse
from pymusicassembler.constants import WORK_BASE

PLAYER = r"^\(?music_assembler(/mc)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a Music Assembler ``.sid`` and the build it decoded."""
    tune = parse(data)
    player = Player(tune, subtune=song - 1)
    cadence = playroutine_cadence(data, subtune=song - 1)
    base = player.base
    info = {
        "load_offset": None if base is None else tune.load_addr - base,
        "relocated": None if base is None else base != WORK_BASE,
        "tempo": player.tempo_reload,
        "rsid": data[:4] == b"RSID",
        "trigger": cadence.source and cadence.source.value,
        "speed": round(PAL_CYCLES_PER_FRAME / cadence.cycles_per_call),
    }
    return np.asarray(player.render_grid(nframes), np.int16), info
