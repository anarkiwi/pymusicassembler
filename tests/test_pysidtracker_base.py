"""pysidtracker base-library integration: shared errors and parser API."""

import pysidtracker

from pymusicassembler.errors import (
    MusicAssemblerError,
    SidParseError,
    SongValidationError,
)
from pymusicassembler.reader import MusicAssemblerSidParser, parse


def test_errors_subclass_pysidtracker_sid_error():
    """The package error hierarchy re-parents onto pysidtracker.SidError."""
    assert issubclass(MusicAssemblerError, pysidtracker.SidError)
    assert issubclass(SidParseError, pysidtracker.SidError)
    assert issubclass(SongValidationError, pysidtracker.SidError)


def test_parser_read_matches_parse(tune_path):
    """MusicAssemblerSidParser().read(path) matches parse(bytes)."""
    data = tune_path.read_bytes()
    from_parser = MusicAssemblerSidParser().read(tune_path)
    from_func = parse(data)
    assert from_parser.image == from_func.image
    assert from_parser.orderlists == from_func.orderlists
    assert from_parser.patterns == from_func.patterns
    assert from_parser.instruments == from_func.instruments
    assert from_parser.container_header == from_func.container_header


def test_parser_is_base_subclass():
    """The adapter is a pysidtracker.BaseSidParser with the package error."""
    assert issubclass(MusicAssemblerSidParser, pysidtracker.BaseSidParser)
    assert MusicAssemblerSidParser.error_class is SidParseError
