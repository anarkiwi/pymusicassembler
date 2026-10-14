"""pymusicassembler's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import playroutine_cadence
from pysidtracker.registers import PAL_CYCLES_PER_FRAME

from pymusicassembler import Player, parse
from pymusicassembler.constants import DEFAULT_LOAD, NATIVE_PLAY_OFFSET

STANDARD_BASE = DEFAULT_LOAD - NATIVE_PLAY_OFFSET
FEATURES = ("order_tempo", "order_jump", "program_jump")

PLAYER = r"^\(?music_assembler(/mc)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a Music Assembler ``.sid`` and the build it decoded."""
    tune = parse(data)
    player = Player(tune, subtune=song - 1)
    cadence = playroutine_cadence(data, subtune=song - 1)
    info = {
        "load_offset": tune.load_addr - player.base,
        "relocated": player.base != STANDARD_BASE,
        "tempo": player.tempo_reload,
        "features": "+".join(f for f in FEATURES if getattr(player.layout, f)),
        "rsid": data[:4] == b"RSID",
        "trigger": cadence.source and cadence.source.value,
        "speed": round(PAL_CYCLES_PER_FRAME / cadence.cycles_per_call),
    }
    return np.asarray(player.render_grid(nframes), np.int16), info
