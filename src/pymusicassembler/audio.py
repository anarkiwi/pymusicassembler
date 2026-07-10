"""Render Music Assembler songs through an emulated SID to samples or WAV.

Thin wrappers over :mod:`pysidtracker.audio`: a :class:`Song` is flattened to
the shared per-frame ``(reg, val)`` write stream and rendered by the shared
engine. By default the emulated SID is `pyresidfp
<https://pypi.org/project/pyresidfp/>`_ (install the ``audio`` extra, which
pulls ``pysidtracker[audio]``). Any object with ``write_register(reg, value)``,
``clock(timedelta) -> samples`` and a ``sampling_frequency`` attribute may be
passed as ``device`` instead.
"""

from pysidtracker.audio import render_samples as _render_samples
from pysidtracker.audio import render_wav as _render_wav
from pysidtracker.audio import write_wav

from pymusicassembler import constants
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.model import Song
from pymusicassembler.player import iter_frames

__all__ = ["CHIP_MODELS", "render_samples", "render_wav", "write_wav"]

CHIP_MODELS = ("6581", "8580")


def _default_device(model: str, sampling_frequency):
    try:
        from pyresidfp import SoundInterfaceDevice
        from pyresidfp.sound_interface_device import ChipModel
    except ImportError as exc:
        raise MusicAssemblerError(
            "pyresidfp is required to render audio; "
            "install with: pip install pymusicassembler[audio]"
        ) from exc
    chip = {"6581": ChipModel.MOS6581, "8580": ChipModel.MOS8580}[model]
    if sampling_frequency:
        return SoundInterfaceDevice(
            model=chip, sampling_frequency=float(sampling_frequency)
        )
    return SoundInterfaceDevice(model=chip)


def _resolve(device, model: str, sampling_frequency):
    """Validate ``model`` and return ``device`` (creating the default if None)."""
    if model not in CHIP_MODELS:
        raise MusicAssemblerError(f"chip model must be one of {CHIP_MODELS}")
    if device is None:
        return _default_device(model, sampling_frequency)
    return device


def _max_frames(seconds: float, cycles_per_frame: int, clock_frequency: float) -> int:
    return max(1, round(seconds / (cycles_per_frame / clock_frequency)))


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
            song, max_frames=_max_frames(seconds, cycles_per_frame, clock_frequency)
        ),
        model=model,
        sampling_frequency=sampling_frequency,
        cycles_per_frame=cycles_per_frame,
        clock_frequency=clock_frequency,
        device=device,
    )
    return samples, float(device.sampling_frequency)


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
            song, max_frames=_max_frames(seconds, cycles_per_frame, clock_frequency)
        ),
        dst,
        model=model,
        sampling_frequency=sampling_frequency,
        cycles_per_frame=cycles_per_frame,
        clock_frequency=clock_frequency,
        device=device,
    )
