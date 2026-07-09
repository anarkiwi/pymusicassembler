"""Exceptions raised by pymusicassembler."""

from pysidtracker import SidError


class MusicAssemblerError(SidError):
    """Base class for all pymusicassembler errors."""


class SidParseError(MusicAssemblerError):
    """A PSID/PRG image (or byte string) could not be parsed."""


class SongValidationError(MusicAssemblerError):
    """A Song does not satisfy the Music Assembler format limits."""
