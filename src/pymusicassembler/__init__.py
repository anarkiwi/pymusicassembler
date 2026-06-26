"""Read, write, play, and render Music Assembler SID songs."""

from pymusicassembler.audio import render_samples, render_wav, write_wav
from pymusicassembler.errors import (
    MusicAssemblerError,
    SidParseError,
    SongValidationError,
)
from pymusicassembler.model import (
    DurationEvent,
    Instrument,
    InstrumentEvent,
    NoteEvent,
    OrderEntry,
    Orderlist,
    Pattern,
    Song,
)
from pymusicassembler.player import Player, iter_frames, render_grid
from pymusicassembler.reader import parse, read
from pymusicassembler.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)
from pymusicassembler.writer import build, validate, write

__version__ = "0.1.0"

__all__ = [
    "DurationEvent",
    "Instrument",
    "InstrumentEvent",
    "MusicAssemblerError",
    "NoteEvent",
    "OrderEntry",
    "Orderlist",
    "Pattern",
    "Player",
    "RegWrite",
    "SidParseError",
    "Song",
    "SongValidationError",
    "__version__",
    "build",
    "iter_frames",
    "iter_register_writes",
    "parse",
    "read",
    "read_reglog",
    "render_grid",
    "render_samples",
    "render_wav",
    "validate",
    "write",
    "write_reglog",
    "write_wav",
]
