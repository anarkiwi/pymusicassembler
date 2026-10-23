"""Render Music Assembler songs through an emulated SID to samples or WAV.

Thin wrappers over :func:`pysidtracker.audio.render_player_samples` /
:func:`~pysidtracker.audio.render_player_wav` driving a :class:`Player`; the
default device is pyresidfp, built at the render's clock. Any object with
``write_register(reg, value)``, ``clock(timedelta) -> samples`` and a
``sampling_frequency`` attribute may be passed as ``device`` instead.
"""

from pysidtracker.audio import (
    CHIP_MODELS,
    render_player_samples,
    render_player_wav,
    write_wav,
)
from pysidtracker.errors import AudioUnavailable

from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.model import Song
from pymusicassembler.player import Player

__all__ = ["CHIP_MODELS", "render_samples", "render_wav", "write_wav"]


def _render(render, song: Song, *args, **options):
    """``render(Player(song), *args, **options)``, its model/backend errors raised
    as :class:`MusicAssemblerError`."""
    try:
        return render(Player(song), *args, **options)
    except (ValueError, AudioUnavailable) as exc:
        raise MusicAssemblerError(str(exc)) from exc


def render_samples(song: Song, seconds: float = 60.0, **options):
    """Render ``seconds`` of ``song`` on an emulated SID.

    Returns ``(samples, sampling_frequency)``, signed 16-bit mono. ``options``
    are those of :func:`pysidtracker.audio.render_player_samples` (``model``,
    ``sampling_frequency``, ``device``, ``cycles_per_frame``, ``clock_frequency``,
    ``progress``).
    """
    return _render(render_player_samples, song, seconds=seconds, **options)


def render_wav(song: Song, dst, seconds: float = 60.0, **options):
    """Render ``seconds`` of ``song`` to a WAV file at ``dst``; returns the path."""
    return _render(render_player_wav, song, dst, seconds=seconds, **options)
