"""Read, write, play, and render Music Assembler SID songs."""

from pymusicassembler.audio import render_samples, render_wav, write_wav
from pymusicassembler.errors import (
    MusicAssemblerError,
    MusicAssemblerParseError,
    SidParseError,
    SongValidationError,
)
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    DurationEvent,
    Instrument,
    InstrumentEvent,
    NoteEvent,
    OrderEntry,
    Orderlist,
    Pattern,
    PresetBank,
    Song,
)
from pymusicassembler.player import Player, iter_frames, render_grid
from pymusicassembler.reader import (
    MusicAssemblerSidParser,
    parse,
    parse_presets,
    read,
    read_presets,
)
from pymusicassembler.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)
from pymusicassembler.writer import (
    build,
    build_native,
    build_presets,
    validate,
    write,
    write_presets,
)

__version__ = "0.7.0"

__all__ = [
    "Arpeggio",
    "ArpeggioRow",
    "DurationEvent",
    "Instrument",
    "InstrumentEvent",
    "MusicAssemblerError",
    "MusicAssemblerParseError",
    "MusicAssemblerSidParser",
    "NoteEvent",
    "OrderEntry",
    "Orderlist",
    "Pattern",
    "Player",
    "PresetBank",
    "RegWrite",
    "SidParseError",
    "Song",
    "SongValidationError",
    "__version__",
    "build",
    "build_native",
    "build_presets",
    "iter_frames",
    "iter_register_writes",
    "parse",
    "parse_presets",
    "read",
    "read_presets",
    "read_reglog",
    "render_grid",
    "render_samples",
    "render_wav",
    "validate",
    "write",
    "write_presets",
    "write_reglog",
    "write_wav",
]
