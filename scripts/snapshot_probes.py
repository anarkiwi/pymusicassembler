"""``pysidtracker snapshot`` probes of a Music Assembler tune beyond the format's own.

The relocated ``S.`` export, the discovered layout and the stream listing:
``PYTHONPATH=scripts pysidtracker snapshot build CORPUS... -o snap.json
--format musicassembler --probe relocated=snapshot_probes:relocated``.
"""

from pymusicassembler import grammar as _grammar
from pymusicassembler import native

RELOCATED_BASE = 0xC000


def relocated(tune) -> bytes:
    """The editor ``S.`` song body relocated to :data:`RELOCATED_BASE`."""
    return native.assemble(tune.song, RELOCATED_BASE)[1]


def layout(tune):
    """The discovered player :class:`~pymusicassembler.layout.Layout`."""
    return tune.song.layout


def grammar(tune) -> tuple:
    """The ``grammar`` subcommand's listing of every song stream."""
    song = tune.song
    return tuple(_grammar.GRAMMAR.listing(_grammar.streams(song, song.layout)))
