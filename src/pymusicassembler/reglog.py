"""SID register write logs.

Thin Music Assembler wrapper over the shared :mod:`pysidtracker.reglog`
surface: one :class:`RegWrite` per SID register write with an absolute clock
in C64 CPU cycles, serialized to plain ``clock reg val`` text lines.
:func:`iter_register_writes` drives the shared
:func:`~pysidtracker.reglog.register_writes_from_player` framer with this
package's :class:`~pymusicassembler.player.Player`.
"""

from typing import Iterator

from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    read_reglog,
    register_writes_from_player,
    write_reglog,
)

from pymusicassembler import constants
from pymusicassembler.model import Song
from pymusicassembler.player import Player

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
    (default one minute at 50 Hz). The player's post-init register file is the
    clock-0 baseline; each subsequent frame's writes are ``cycles_per_frame``
    apart with in-frame writes ``write_spacing`` cycles apart. Raises
    :class:`pysidtracker.SidParseError` if the spacing overruns one frame.
    """
    return register_writes_from_player(
        Player(song), max_frames, cycles_per_frame, write_spacing
    )
