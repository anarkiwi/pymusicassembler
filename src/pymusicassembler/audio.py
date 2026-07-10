"""Render Music Assembler songs through an emulated SID to samples or WAV.

Thin wrappers over :mod:`pysidtracker.audio`: a :class:`Song` is flattened to
the shared per-frame ``(reg, val)`` write stream and rendered by the shared
engine. By default the emulated SID is `pyresidfp
<https://pypi.org/project/pyresidfp/>`_ (install the ``audio`` extra, which
pulls ``pysidtracker[audio]``). Any object with ``write_register(reg, value)``,
``clock(timedelta) -> samples`` and a ``sampling_frequency`` attribute may be
passed as ``device`` instead.
"""

from pysidtracker.audio import (
    CHIP_MODELS,
    device_sampling_frequency,
    resolve_device,
    seconds_to_frames,
    write_wav,
)
from pysidtracker.audio import render_samples as _render_samples
from pysidtracker.audio import render_wav as _render_wav
from pysidtracker.errors import AudioUnavailable

from pymusicassembler import constants
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.model import Song
from pymusicassembler.player import iter_frames

__all__ = ["CHIP_MODELS", "render_samples", "render_wav", "write_wav"]


def _resolve(device, model: str, sampling_frequency):
    """Validate ``model`` and return ``device`` (creating the default if None).

    Wraps the shared :func:`pysidtracker.audio.resolve_device`, re-raising its
    model :class:`ValueError` and missing-pyresidfp
    :class:`~pysidtracker.errors.AudioUnavailable` as :class:`MusicAssemblerError`.
    """
    try:
        return resolve_device(device, model, sampling_frequency)
    except ValueError as exc:
        raise MusicAssemblerError(str(exc)) from exc
    except AudioUnavailable as exc:
        raise MusicAssemblerError(
            "pyresidfp is required to render audio; "
            "install with: pip install pymusicassembler[audio]"
        ) from exc


def render_samples(
    song: Song,
    seconds: float = 60.0,
    model: str = "8580",
    sampling_frequency=None,
    device=None,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    clock_frequency: float = constants.PAL_CLOCK_HZ,
):
    """Render ``song`` on an emulated SID.

    Returns ``(samples, sampling_frequency)`` where samples are signed 16-bit
    mono. Rendering stops at ``seconds`` (the player loops, so a duration is
    required).
    """
    device = _resolve(device, model, sampling_frequency)
    samples = _render_samples(
        iter_frames(
            song,
            max_frames=seconds_to_frames(seconds, cycles_per_frame, clock_frequency),
        ),
        model=model,
        sampling_frequency=sampling_frequency,
        cycles_per_frame=cycles_per_frame,
        clock_frequency=clock_frequency,
        device=device,
    )
    return samples, device_sampling_frequency(device)


def render_wav(
    song: Song,
    dst,
    seconds: float = 60.0,
    model: str = "8580",
    sampling_frequency=None,
    device=None,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    clock_frequency: float = constants.PAL_CLOCK_HZ,
):
    """Render ``song`` to a WAV file at ``dst``; returns the path written."""
    device = _resolve(device, model, sampling_frequency)
    return _render_wav(
        iter_frames(
            song,
            max_frames=seconds_to_frames(seconds, cycles_per_frame, clock_frequency),
        ),
        dst,
        model=model,
        sampling_frequency=sampling_frequency,
        cycles_per_frame=cycles_per_frame,
        clock_frequency=clock_frequency,
        device=device,
    )
