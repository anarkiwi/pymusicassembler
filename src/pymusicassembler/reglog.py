"""SID register write logs.

Thin Music Assembler wrapper over the shared :mod:`pysidtracker.reglog`
surface: one :class:`RegWrite` per SID register write with an absolute clock
in C64 CPU cycles, serialized to plain ``clock reg val`` text lines.
:func:`iter_register_writes` flattens the player's per-frame output through the
shared :func:`~pysidtracker.reglog.frame_writes` framer.
"""

from typing import Iterator

from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    frame_writes,
    read_reglog,
    write_reglog,
)

from pymusicassembler import constants
from pymusicassembler.model import Song
from pymusicassembler.player import iter_frames

__all__ = [
    "DEFAULT_WRITE_SPACING",
    "RegWrite",
    "iter_register_writes",
    "read_reglog",
    "write_reglog",
]


def iter_register_writes(
    song: Song,
    max_frames: int = 50 * 60,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``song``, frame by frame.

    The Music Assembler player loops forever, so ``max_frames`` bounds the log
    (default one minute at 50 Hz). The player already emits ``0..24`` register
    offsets, so framing runs with ``sid_reg_base=0``; frames are
    ``cycles_per_frame`` apart and in-frame writes ``write_spacing`` cycles
    apart. Raises :class:`pysidtracker.SidParseError` if the spacing overruns
    one frame.
    """
    return frame_writes(
        iter_frames(song, max_frames=max_frames),
        cycles_per_frame=cycles_per_frame,
        write_spacing=write_spacing,
        sid_reg_base=0,
        reg_count=constants.SID_REGISTERS,
    )
