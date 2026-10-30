"""Temporary: where does the player diverge from the oracle on irq_digimix?"""

import sys

import numpy as np

import oracle_hvsc
from pysidtracker import start_subtune
from pysidtracker.testing import default_cache, oracle_grid, resolve_tune, song_frames

REL = "MUSICIANS/A/Albartus_Jan/Play_the_Mix.sid"
path = resolve_tune(REL)
data = path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read()
frames = song_frames(data, None)
want = np.asarray(
    oracle_grid(path, oracle_cache=default_cache("csv") / "songs", frames=frames)
)
got = oracle_hvsc.render(data, start_subtune(data) + 1, frames + 4)[0]
print(sys.argv[1:], "frames", frames, want.shape, got.shape, flush=True)
for lead in range(5):
    seg = got[lead : lead + len(want)]
    bad = np.flatnonzero((seg != want).any(1))
    print("lead", lead, "mismatching frames", len(bad), "first", bad[:5], flush=True)
    if len(bad):
        f = bad[0]
        for g in range(max(0, f - 1), f + 2):
            print(" frame", g, "want", want[g].tolist())
            print(" frame", g, "got ", seg[g].tolist())
