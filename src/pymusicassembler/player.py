"""Music Assembler playroutine: ``specs/musicassembler.play`` run on a tune's init.

The tune's own init runs in the 6502 emulator as the PSID driver boots it
(power-on RAM, PAL/NTSC flag, banking; selecting the subtune and placing any
relocated data); each play call is then the playspec in
:mod:`pymusicassembler.playspec` run on the player's work cells, at the
addresses :mod:`pymusicassembler.layout` finds. A SID with play address 0
drives the player from the interrupts its init installs (with any digi on
another source or a main loop), so it plays on pysidtracker's
:class:`~pysidtracker.irqhost.InterruptPlayer`, framed at its play source's
raises as the oracle is.
"""

from typing import List, Optional

from pysidtracker import PSID_MAGIC, RSID_MAGIC, SidParseError, parse_sid_header
from pysidtracker import psid_init_bank, psid_play_bank, run_to_rts, set_iomap
from pysidtracker import wire_mpu
from pysidtracker.irqhost import InterruptPlayer
from pysidtracker.oracle import PAL_FLAG
from pysidtracker.playspec import SpecPlayer
from pysidtracker.registers import sid_model_for_flags

from pymusicassembler import constants, layout, playspec
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.model import Song

MAX_CYCLES = 8_000_000


class Player(SpecPlayer):
    """Play a :class:`~pymusicassembler.model.Song` one play call at a time.

    ``subtune`` (0-based) is passed to the tune's init in the accumulator. The
    container header, if any, sets the SID model and the PAL/NTSC flag. ``bind``
    resolves the spec's layout from the 64 KiB view and the play entry; with
    ``validate`` the streams the spec opens are parsed under the song grammar.
    """

    def __init__(
        self, song: Song, subtune: int = 0, bind=playspec.binding, validate=False
    ):
        self._song = song
        self._subtune = subtune
        self._layouts = {}
        self._hosted: Optional[InterruptPlayer] = None
        sid = song.container_header[:4] in (PSID_MAGIC, RSID_MAGIC)
        header = parse_sid_header(song.container_header) if sid else None
        if header is not None and not song.play_addr:
            self._hosted = InterruptPlayer(song.container_header + song.image, subtune)
        super().__init__(
            playspec.SPEC,
            song.image,
            song.load_addr,
            song.init_addr,
            song.play_addr or song.init_addr,
            subtune,
            bind=bind,
            validate=validate,
            entries=playspec.entries,
            psid=header is None or header.is_psid,
            max_cycles=MAX_CYCLES,
            sid_model=sid_model_for_flags(header.flags) if header else "6581",
            ntsc=bool(header and header.is_ntsc),
        )

    def _init(self, subtune: int) -> None:
        """Run init as psiddrv does, banking ``$01`` only where it would.

        An RSID tune boots under ``$37`` and, like a PSID whose init installs
        its own interrupt (play 0), plays under the banking its init left
        (``psiddrv::iomap`` 0).
        """
        if self._pal_flag is not None:
            self._mem[PAL_FLAG] = self._pal_flag
        self._mem[1] = psid_init_bank(self._init_addr) if self._psid else 0x37
        self._mpu, self._obs = wire_mpu(self._mem, self._illegal, self._sid_model)
        run_to_rts(self._mpu, self._obs, self._init_addr, subtune, self._max_cycles)
        if self._psid and self._song.play_addr:
            set_iomap(self._mpu, psid_play_bank(self._play_addr))
        self._due = self._mpu.processorCycles
        self._bus = self._obs._subject  # pylint: disable=protected-access
        play = self._play_addr
        self.entry = play if play in self._entries() else self._probe(play)
        self.layout = self._entry_layout(subtune)

    def _entry_layout(self, subtune: int) -> layout.Layout:
        """The layout of the play entry: the song's, when its post-init discovery
        was for ``subtune``, else the instance the play routine reached."""
        song = self._song
        if song.layout_post_init and subtune == song.layout_subtune:
            return song.layout
        for (_, entry), found in self._layouts.items():
            if entry == self.entry:
                return found
        return self._discover(self.entry)

    def _discover(self, entry: int) -> layout.Layout:
        """The layout of the instance at ``entry`` under the current banking."""
        key = (self._bus.bits, entry)
        if key not in self._layouts:
            self._layouts[key] = layout.discover(self.view(), entry)
        return self._layouts[key]

    def _call(self, play: int, probe: bool = False) -> Optional[int]:
        entry = super()._call(play, probe)
        if probe and entry is not None:
            self._discover(entry)
        return entry

    def _instance(self, entry: int) -> tuple:
        self._discover(entry)
        return super()._instance(entry)

    @property
    def base(self) -> int:
        """Native player base: the play entry sits at ``base + $21``."""
        return self.layout.entry - constants.NATIVE_PLAY_OFFSET

    @property
    def tempo_reload(self) -> int:
        """The play-call divider reload (``LDA #n`` at the play entry's BMI target)."""
        return self._bus[self.layout.tempo_reload]

    def _probe(self, play: int) -> Optional[int]:
        """The play entry the play routine calls first; else the one its IRQ
        vectors or nearest code reach (``None`` raises, as no entry is playable)."""
        found = super()._probe(play) if self._song.play_addr else None
        if found is None:
            found = layout.resolve_entry(self.view(), self._song.play_addr)
        if found is None:
            raise MusicAssemblerParseError("no play entry after init")
        return found

    def _frame(self) -> None:
        if self._hosted is not None:
            self._hosted.play_frame()
        elif not self._song.play_addr:
            self._run(self.entry)
        else:
            super()._frame()

    def snapshot(self) -> List[int]:
        if self._hosted is None:
            return super().snapshot()
        return self._hosted.snapshot()

    def render_grid(self, nframes: int) -> List[List[int]]:
        """``nframes`` rows; a hosted tune's are framed at its play source's raises."""
        if self._hosted is None:
            return super().render_grid(nframes)
        try:
            return self._hosted.render_grid(nframes)
        except SidParseError as exc:
            raise MusicAssemblerParseError(str(exc)) from exc


def iter_frames(song: Song, max_frames: Optional[int] = None):
    """Yield per-call register write lists for ``song`` (see :class:`MemPlayer`)."""
    return Player(song).iter_frames(max_frames)


def render_grid(song: Song, nframes: int) -> List[List[int]]:
    """Render ``nframes`` forward-filled per-call register snapshots."""
    return Player(song).render_grid(nframes)
