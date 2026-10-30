"""Register Music Assembler with the generic ``pysidtracker`` tooling.

``musicassembler`` on the ``pysidtracker.formats`` entry-point group plays tunes
through :class:`~pymusicassembler.player.Player` for ``pysidtracker info|reglog|wav``
and is the portable side spec ``format:musicassembler`` of ``pysidtracker.pathdiff``;
its ``build``, ``native`` and ``grammar`` probes are the rebuilt image, editor
``S.`` song and stream listing ``pysidtracker snapshot`` digests, and its
``grammar``/``streams`` hooks back ``pysidtracker grammar``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable, NamedTuple, Optional, Sequence

from pysidtracker import PSID_MAGIC, RSID_MAGIC, SidFormat, parse_sid_header
from pysidtracker.formats import SpecBinding

from pymusicassembler import grammar, native, playspec, writer
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


def _build(tune: Tune) -> bytes:
    return writer.build(tune.song)


def _native(tune: Tune) -> bytes:
    return native.assemble(tune.song)[1]


def _streams(tune: Tune):
    return grammar.streams(tune.song, tune.song.layout)


def _listing(tune: Tune) -> tuple:
    return tuple(MUSIC_ASSEMBLER.listing(tune))


def _spec(tune: Tune) -> SpecBinding:
    """The playspec of ``tune``'s own image: the variant :func:`playspec.bind`
    chooses for the instance its init leaves (``playspec.has_native`` tells whether
    it still holds a ``native`` hook)."""
    song = tune.song
    booted = Player(song, subtune=tune.subtune)
    bound = lru_cache(maxsize=2)(playspec.bind)
    spec, _ = bound(booted.view(), booted.entry)

    def bind(view, entry):
        chosen, found = bound(view, entry)
        return chosen, dict(found)

    sid = song.container_header[:4] in (PSID_MAGIC, RSID_MAGIC)
    header = parse_sid_header(song.container_header) if sid else None
    return SpecBinding(
        spec=spec,
        bind=bind,
        image=song.image,
        entries=playspec.entries,
        subtune=tune.subtune,
        psid=header is None or header.is_psid,
        sid=song.container_header + song.image if header else None,
    )


MUSIC_ASSEMBLER = SidFormat(
    name="musicassembler",
    parser=MusicAssemblerTuneParser(),
    player=player,
    describe=_describe,
    metadata=_metadata,
    probes={"build": _build, "native": _native, "grammar": _listing},
    grammar=grammar.GRAMMAR,
    streams=_streams,
    spec=_spec,
)
