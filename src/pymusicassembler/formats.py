"""Register Music Assembler with the generic ``pysidtracker`` tooling.

``musicassembler`` on the ``pysidtracker.formats`` entry-point group plays tunes
through :class:`~pymusicassembler.player.Player` for ``pysidtracker info|reglog|wav``
and is the portable side spec ``format:musicassembler`` of ``pysidtracker.pathdiff``.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional, Sequence

from pysidtracker import SidFormat

from pymusicassembler.model import Song
from pymusicassembler.player import Player
from pymusicassembler.reader import MusicAssemblerSidParser, parse


class Tune(NamedTuple):
    """A parsed song and the 0-based subtune its player starts."""

    song: Song
    subtune: int = 0


class MusicAssemblerTuneParser(MusicAssemblerSidParser):
    """:class:`MusicAssemblerSidParser` yielding a :class:`Tune` of ``subtune``."""

    def parse(self, data: bytes, subtune: Optional[int] = None, **kwargs) -> Tune:
        return Tune(parse(data), subtune or 0)


def player(tune: Tune) -> Player:
    """A fresh :class:`Player` of ``tune``'s subtune."""
    return Player(tune.song, subtune=tune.subtune)


def _metadata(tune: Tune) -> Sequence:
    song = tune.song
    return (
        song.name,
        song.author,
        song.released,
        song.load_addr,
        song.init_addr,
        song.play_addr,
    )


def _describe(tune: Tune) -> Iterable[str]:
    song = tune.song
    yield f"patterns: {len(song.patterns)}"
    yield f"instruments: {len(song.instruments)}"


MUSIC_ASSEMBLER = SidFormat(
    name="musicassembler",
    parser=MusicAssemblerTuneParser(),
    player=player,
    describe=_describe,
    metadata=_metadata,
)
