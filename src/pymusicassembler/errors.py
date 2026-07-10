"""Exceptions raised by pymusicassembler."""

from pysidtracker import make_package_errors

# ``SidParseError`` subclasses BOTH the package root and the base
# ``pysidtracker.SidParseError`` (via ``make_package_errors``), so a base
# ``except SidParseError`` catches a Music Assembler parse error too.
MusicAssemblerError, SidParseError, _FormatError = make_package_errors("MusicAssembler")


class SongValidationError(MusicAssemblerError):
    """A Song does not satisfy the Music Assembler format limits."""
