"""Typed models: the Music Assembler song and the editor's P. preset bank."""

from dataclasses import astuple, dataclass, field
from typing import Any, List, Optional

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
    """One voice's orderlist: its entries and how it ends on disk.

    ``terminator`` marks the ending -- ``ORDER_END`` (``$FF``),
    ``SILENCE_PATTERN`` (a single-byte ``$FE``), or ``ORDER_JUMP``
    (``$FD n``) -- so the writer emits the last entry verbatim, no ``$FF``.
    """

    entries: List[OrderEntry] = field(default_factory=list)
    terminator: int = constants.ORDER_END

    def to_bytes(self) -> bytes:
        """The on-disk bytes: the entries, ending in exactly the terminator.

        A ``$FE`` silence is the last entry's single pattern-id byte and a
        ``$FD n`` jump its two bytes; only an ``$FF`` ending appends a byte.
        """
        out = []
        last = len(self.entries) - 1
        for i, entry in enumerate(self.entries):
            if i == last and self.terminator == constants.SILENCE_PATTERN:
                out.append(entry.pattern_id & 0xFF)
            else:
                out.extend((entry.pattern_id & 0xFF, entry.control_byte()))
        if self.terminator == constants.ORDER_END:
            out.append(constants.ORDER_END)
        return bytes(out)


@dataclass(frozen=True)
class NoteEvent:
    """A note row: ``note`` (+ orderlist transpose) indexes the freq table."""

    note: int
    control: int  # the trailing note-control byte ($1141)
    args: tuple = ()  # inline filter/vibrato argument bytes

    @property
    def duration(self) -> int:
        """Rows the note lasts (the control byte's low five bits)."""
        return self.control & constants.CTL_DUR_MASK

    @property
    def tied(self) -> bool:
        """The note changes pitch without retriggering the instrument."""
        return bool(self.control & constants.CTL_TRIGGERED)

    @property
    def vibrato(self) -> bool:
        """The vibrato runs in place of the instrument's arpeggio."""
        return bool(self.control & constants.CTL_VIBARP)

    @property
    def filter(self) -> bool:
        """The note takes the filter (``args``: start cutoff, sweep length)."""
        return bool(self.control & constants.CTL_FILTER)


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
        """The pattern's rows as typed events, parsed with the pattern grammar.

        Raises :class:`~pymusicassembler.errors.MusicAssemblerParseError` when
        ``data`` plus the terminator is not exactly one pattern.
        """
        from pymusicassembler import grammar  # the grammar builds model objects

        return grammar.parse_exact(self.to_bytes(), grammar.PATTERN)


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
        """The 8 on-disk record bytes, in field order."""
        return bytes(v & 0xFF for v in astuple(self))

    @classmethod
    def from_bytes(cls, raw) -> "Instrument":
        """Decode a record; missing trailing bytes read as zero."""
        size = constants.INSTR_RECORD_SIZE
        return cls(*bytes(raw)[:size].ljust(size, b"\0"))


@dataclass(frozen=True)
class ArpeggioRow:
    """One row of an arpeggio program: (wave, note, filter).

    ``wave`` is ANDed with the voice's control mask and written to the
    waveform register; ``note`` indexes the note-frequency tables (top bit
    set: absolute index on ``pitch_skip`` builds); ``filter`` is the cutoff written when non-zero.
    """

    wave: int
    note: int
    filter: int

    def to_bytes(self) -> bytes:
        """The three on-disk row bytes."""
        return bytes((self.wave & 0xFF, self.note & 0xFF, self.filter & 0xFF))


@dataclass
class Arpeggio:
    """An arpeggio (inner) program: its rows, then the terminating marker.

    ``terminator`` is :data:`constants.PROGRAM_RESTART` ($FF, loop to the
    first row) or :data:`constants.PROGRAM_STOP` ($FE, end); on the later
    builds it is :data:`constants.PROGRAM_JUMP` ($FD) and ``jump`` the row continued at.
    """

    rows: List[ArpeggioRow] = field(default_factory=list)
    terminator: int = constants.PROGRAM_RESTART
    jump: Optional[int] = None

    def to_bytes(self) -> bytes:
        """The program's on-disk bytes: its rows, then the terminator."""
        out = bytearray(b for row in self.rows for b in row.to_bytes())
        if self.terminator == constants.PROGRAM_JUMP:
            out.extend((constants.PROGRAM_JUMP, (self.jump or 0) & 0xFF))
        else:
            out.append(self.terminator & 0xFF)
        return bytes(out)


@dataclass
class PresetBank:
    """The editor's ``P.`` preset bank: 16 arpeggio slots + 32 presets.

    ``arpeggios[i]`` is the i-th program (``None`` = its empty zero slot);
    ``presets`` lay out like the song's instrument table.  Disk form: the
    768-byte body at ``$4300`` under a two-byte load-address prefix.
    """

    arpeggios: List[Optional[Arpeggio]] = field(
        default_factory=lambda: [None] * constants.ARP_COUNT
    )
    presets: List[Instrument] = field(
        default_factory=lambda: [
            Instrument() for _ in range(constants.PRESET_BANK_PRESETS)
        ]
    )

    def to_bytes(self) -> bytes:
        """The bank's 768-byte body: zero-padded arpeggio slots, then presets."""
        if len(self.arpeggios) != constants.ARP_COUNT:
            raise MusicAssemblerError(
                f"preset bank needs {constants.ARP_COUNT} arpeggio slots, "
                f"got {len(self.arpeggios)}"
            )
        if len(self.presets) != constants.PRESET_BANK_PRESETS:
            raise MusicAssemblerError(
                f"preset bank needs {constants.PRESET_BANK_PRESETS} presets, "
                f"got {len(self.presets)}"
            )
        out = bytearray()
        for i, prog in enumerate(self.arpeggios):
            data = b"" if prog is None else prog.to_bytes()
            if len(data) > constants.PRESET_BANK_ARP_SLOT:
                raise MusicAssemblerError(
                    f"arpeggio slot {i} needs {len(data)} bytes, "
                    f"the bank slot holds {constants.PRESET_BANK_ARP_SLOT}"
                )
            out.extend(data)
            out.extend(b"\x00" * (constants.PRESET_BANK_ARP_SLOT - len(data)))
        out += b"".join(preset.to_bytes() for preset in self.presets)
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PresetBank":
        """Parse the bank's 768-byte body into a typed :class:`PresetBank`."""
        if len(data) != constants.PRESET_BANK_BODY:
            raise MusicAssemblerError(
                f"preset bank body must be {constants.PRESET_BANK_BODY} bytes, "
                f"got {len(data)}"
            )
        from pymusicassembler import grammar  # the grammar builds model objects

        arpeggios: List[Optional[Arpeggio]] = []
        slot_len = constants.PRESET_BANK_ARP_SLOT
        for i in range(constants.ARP_COUNT):
            slot = data[i * slot_len : (i + 1) * slot_len]
            try:
                arpeggios.append(
                    grammar.parse_prefix(slot, grammar.PROGRAM_JUMP).model
                    if any(slot)
                    else None
                )
            except MusicAssemblerError as exc:
                raise MusicAssemblerError(f"arpeggio slot {i}: {exc}") from exc
        arps_off = constants.ARP_COUNT * slot_len
        rec = constants.INSTR_RECORD_SIZE
        presets = [
            Instrument.from_bytes(data[arps_off + i * rec : arps_off + (i + 1) * rec])
            for i in range(constants.PRESET_BANK_PRESETS)
        ]
        return cls(arpeggios=arpeggios, presets=presets)


@dataclass
class Song:
    """A complete Music Assembler song.

    The load address, the three per-voice orderlists, the shared pattern
    pool, the 8-byte instruments, the note-frequency tables, and the 16
    arpeggio programs (a ``None`` slot = pointer-table placeholder here).
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
    inner_programs: List[Optional[Arpeggio]] = field(
        default_factory=lambda: [None] * constants.ARP_COUNT
    )
    # Raw whole-image bytes (player + data) for byte-stable re-emission.
    image: bytes = b""
    # Original PSID/RSID container bytes before the image; empty for a bare ``.prg``.
    container_header: bytes = b""
    # The player layout the reader located, and whether it was found in memory
    # after init of ``layout_subtune`` ran (not in the static image); ``Player``
    # reuses it only for that subtune.
    layout: Any = field(default=None, repr=False, compare=False)
    layout_post_init: bool = field(default=False, repr=False, compare=False)
    layout_subtune: int = field(default=0, repr=False, compare=False)

    def apply_preset_bank(self, bank: PresetBank) -> None:
        """Swap in the bank's presets and arpeggios.

        The editor's "play back your music using different preset banks":
        the song keeps its orderlists and patterns and now references the
        bank's 32 presets and 16 arpeggio programs, ready for :func:`build`.
        """
        self.instruments = list(bank.presets)
        self.inner_programs = list(bank.arpeggios)
