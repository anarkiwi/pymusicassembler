"""Temporary: where does the player diverge from the oracle on irq_digimix?"""

import numpy as np

import oracle_hvsc
from pysidtracker import start_subtune
from pysidtracker.irqhost import InterruptPlayer, TunePlayer
from pysidtracker.testing import default_cache, oracle_grid, resolve_tune, song_frames

REL = "MUSICIANS/A/Albartus_Jan/Play_the_Mix.sid"
path = resolve_tune(REL)
data = open(path, "rb").read()
frames = song_frames(data, None)
want = np.asarray(
    oracle_grid(path, oracle_cache=default_cache("csv") / "songs", frames=frames)
)
sub = start_subtune(data)


def show(name, got):
    got = np.asarray(got)
    for lead in range(3):
        seg = got[lead : lead + len(want)]
        if len(seg) < len(want):
            continue
        diff = seg != want
        bad = np.flatnonzero(diff.any(1))
        print(
            name,
            "lead",
            lead,
            "bad frames",
            len(bad),
            "regs",
            np.flatnonzero(diff.any(0)).tolist(),
            flush=True,
        )
        for f in bad[:6]:
            print(
                "  ",
                f,
                [(r, int(want[f][r]), int(seg[f][r])) for r in np.flatnonzero(diff[f])],
                flush=True,
            )


show("player", oracle_hvsc.render(data, sub + 1, frames + 4)[0])
show("irqhost", InterruptPlayer(data, sub).render_grid(frames + 4))
show("tuneplayer", TunePlayer(data, sub).render_grid(frames + 4))
