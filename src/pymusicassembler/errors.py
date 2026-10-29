"""Exceptions for pymusicassembler, built by :func:`pysidtracker.make_package_errors`.

``MusicAssemblerParseError`` subclasses both ``MusicAssemblerError`` and
:class:`pysidtracker.SidParseError` ("not a decodable Music Assembler tune");
``SidParseError`` is kept as its backward-compatible alias.
"""

from contextlib import contextmanager

from pysidtracker import make_package_errors

MusicAssemblerError, MusicAssemblerParseError, _FormatError = make_package_errors(
    "MusicAssembler"
)
SidParseError = MusicAssemblerParseError


class SongValidationError(MusicAssemblerError):
    """A Song does not satisfy the Music Assembler format limits."""


@contextmanager
def unwritable():
    """Re-raise a parse error met while writing as :class:`SongValidationError`."""
    try:
        yield
    except MusicAssemblerParseError as exc:
        raise SongValidationError(str(exc)) from exc
