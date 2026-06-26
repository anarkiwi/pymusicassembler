"""Typed model of a Music Assembler song.

A :class:`Song` holds everything needed to play and re-emit a Music
Assembler tune: three per-voice orderlists, the shared pattern pool,
8-byte instrument records, the per-tune note-frequency tables and the
instrument inner (effect) programs.

Patterns are kept as RAW byte sequences (the player's own pattern
bytecode, minus the ``$FF`` terminator).  The grammar -- ``<0x60`` note +
control byte, ``0x60-0x7F`` bare duration, ``0x80-0x9F`` instrument
select, ``0xA0-0xFE`` long duration -- is documented on
:meth:`Pattern.events`, which decodes the bytes into typed events for
inspection, while the bytes themselves drive both the player and the
writer so a tune round-trips byte-for-byte.
"""

from dataclasses import dataclass, field
from typing import List

from pymusicassembler import constants
from pymusicassembler.errors import MusicAssemblerError


@dataclass(frozen=True)
class OrderEntry:
    """One orderlist entry: play ``pattern_id`` with a transpose/repeat.

    The on-disk pair is ``(pattern_id, transpose << 4 | repeat)``.
    ``transpose`` is the orderlist transpose added to note indices (0-15);
    ``repeat`` is the raw 0-15 repeat field (the pattern plays
    ``repeat + 1`` times in total, matching the player's repeat counter).
    Pattern id ``0xFE`` is the silence marker (gate off, no row).
    """

    pattern_id: int
    transpose: int = 0
    repeat: int = 0

    def control_byte(self) -> int:
        """The packed ``transpose << 4 | repeat`` control byte."""
        if not 0 <= self.transpose <= 0x0F:
            raise MusicAssemblerError(f"transpose out of range: {self.transpose}")
        if not 0 <= self.repeat <= 0x0F:
            raise MusicAssemblerError(f"repeat out of range: {self.repeat}")
        return (self.transpose << 4) | self.repeat

    @classmethod
    def from_bytes(cls, pattern_id: int, control: int) -> "OrderEntry":
        """Decode an on-disk ``(pattern_id, control)`` pair."""
        return cls(
            pattern_id=pattern_id,
            transpose=(control >> 4) & 0x0F,
            repeat=control & 0x0F,
        )


@dataclass
class Orderlist:
    """One voice's orderlist: a sequence of entries, looping at the end."""

    entries: List[OrderEntry] = field(default_factory=list)


@dataclass(frozen=True)
class NoteEvent:
    """A note row: ``note`` (+ orderlist transpose) indexes the freq table."""

    note: int
    control: int  # the trailing note-control byte ($1141)
    args: tuple = ()  # inline filter/vibrato argument bytes


@dataclass(frozen=True)
class InstrumentEvent:
    """An instrument-select event (``id`` 0-31)."""

    instrument: int


@dataclass(frozen=True)
class DurationEvent:
    """A bare/long duration token (sets the row's frame duration)."""

    duration: int
    long: bool = False  # True for the $A0-$FE long-duration form


PatternEvent = NoteEvent | InstrumentEvent | DurationEvent


@dataclass
class Pattern:
    """A pattern: raw player bytecode (no trailing ``$FF`` terminator)."""

    data: List[int] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        """On-disk pattern bytes including the ``$FF`` terminator."""
        return bytes(list(self.data) + [constants.PATTERN_END])

    def events(self) -> List[PatternEvent]:
        """Decode the raw bytes into typed events (for inspection).

        Mirrors the player's pattern walk: ``$80-$9F`` selects an
        instrument and the following byte may continue into a note;
        ``$A0-$FE`` and ``$60-$7F`` are duration tokens; ``<0x60`` is a
        note whose control byte (and any inline filter/vibrato args)
        follows.  Decoding is best-effort and never consumes past the
        end of ``data``.
        """
        events: List[PatternEvent] = []
        data = self.data
        i = 0
        n = len(data)
        while i < n:
            val = data[i]
            if val >= constants.INSTR_MIN:
                if val >= constants.LONGDUR_MIN:  # long-duration token
                    events.append(
                        DurationEvent(val & constants.CTL_DUR_MASK, long=True)
                    )
                    i += 1
                    continue
                events.append(InstrumentEvent(val & 0x1F))  # instrument select
                i += 1
                if i < n and data[i] >= constants.DUR_MIN:
                    events.append(DurationEvent(data[i] & constants.CTL_DUR_MASK))
                    i += 1
                    continue
                if i >= n:
                    break
                val = data[i]
            elif val >= constants.DUR_MIN:  # bare duration token
                events.append(DurationEvent(val & constants.CTL_DUR_MASK))
                i += 1
                continue
            # a note (val < 0x60)
            note = val
            i += 1
            if i >= n:
                events.append(NoteEvent(note, 0))
                break
            ctl = data[i]
            i += 1
            args: List[int] = []
            if ctl >= constants.CTL_FILTER:
                args = data[i : i + 2]
                i += 2
            elif ctl & constants.CTL_VIBARP:
                args = data[i : i + 2]
                i += 2
            events.append(NoteEvent(note, ctl, tuple(args)))
        return events


@dataclass
class Instrument:
    """An 8-byte instrument record.

    Fields map to the record bytes the player reads: attack/decay,
    sustain/release, waveform, pulse-width init + sweep step, arp
    speed + step, and the porta/effect flags byte.
    """

    attack_decay: int = 0
    sustain_release: int = 0
    waveform: int = 0
    pulse_init: int = 0
    pulse_step: int = 0
    arp_speed: int = 0
    arp_step: int = 0
    porta: int = 0

    def to_bytes(self) -> bytes:
        """The 8 on-disk record bytes."""
        return bytes(
            (
                self.attack_decay & 0xFF,
                self.sustain_release & 0xFF,
                self.waveform & 0xFF,
                self.pulse_init & 0xFF,
                self.pulse_step & 0xFF,
                self.arp_speed & 0xFF,
                self.arp_step & 0xFF,
                self.porta & 0xFF,
            )
        )

    @classmethod
    def from_bytes(cls, raw) -> "Instrument":
        """Decode an 8-byte record."""
        b = list(raw) + [0] * (8 - len(raw))
        return cls(b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7])


@dataclass
class Song:
    """A complete Music Assembler song.

    ``load_addr`` is the C64 load address; ``orderlists`` are the three
    per-voice orderlists; ``patterns`` the shared pattern pool indexed by
    pattern id; ``instruments`` the 8-byte records indexed by instrument
    id; ``freq_lo`` / ``freq_hi`` the per-tune split note-frequency
    tables; ``inner_programs`` the per-instrument effect programs the
    instrument inner-table indexes (kept as raw bytes for round-trip).

    The remaining fields capture the per-tune table base addresses and
    any image bytes the player code/data occupy, so the writer can
    re-emit a byte-stable image.
    """

    # pylint: disable=too-many-instance-attributes  # a full tune is wide
    load_addr: int = constants.DEFAULT_LOAD
    init_addr: int = constants.DEFAULT_INIT
    play_addr: int = constants.DEFAULT_PLAY
    name: str = ""
    author: str = ""
    released: str = ""
    orderlists: List[Orderlist] = field(
        default_factory=lambda: [Orderlist() for _ in range(constants.VOICES)]
    )
    patterns: List[Pattern] = field(default_factory=list)
    instruments: List[Instrument] = field(default_factory=list)
    freq_lo: List[int] = field(default_factory=list)
    freq_hi: List[int] = field(default_factory=list)
    # Raw whole-image bytes (player + data) for byte-stable re-emission.
    image: bytes = b""
    # The original container header (PSID/RSID bytes before the image), kept
    # so a tune read from a ``.sid`` re-emits its exact container; empty for a
    # bare ``.prg`` or a programmatically built song.
    container_header: bytes = b""
