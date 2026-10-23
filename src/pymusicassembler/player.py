"""Music Assembler playroutine on a :class:`pysidtracker.EmuPlayer` boot.

The tune's own init runs in the 6502 emulator as the PSID driver boots it
(power-on RAM, PAL/NTSC flag, banking; selecting the subtune and placing any
relocated data); the play call is then modelled in Python on the player's work
cells, at the addresses :mod:`pymusicassembler.layout` finds. A SID with play
address 0 drives the player from the interrupts its init installs (with any
digi on another source or a main loop), so it plays on pysidtracker's
:class:`~pysidtracker.irqhost.InterruptPlayer`, framed at its play source's
raises as the oracle is.
"""

from typing import List, Optional, Set

from pysidtracker import PSID_MAGIC, RSID_MAGIC, EmuPlayer, parse_sid_header
from pysidtracker import psid_init_bank, psid_play_bank, run_to_rts, set_iomap
from pysidtracker import SidParseError, wire_mpu
from pysidtracker.irqhost import InterruptPlayer
from pysidtracker.oracle import PAL_FLAG
from pysidtracker.registers import AD, CTRL, FC_HI, FREQ_HI, FREQ_LO, PW_HI, PW_LO
from pysidtracker.registers import RES_FILT, SR, sid_model_for_flags

from pymusicassembler import constants, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.model import Song

MAX_CYCLES = 8_000_000


class Player(EmuPlayer):
    """Play a :class:`~pymusicassembler.model.Song` one play call at a time.

    ``subtune`` (0-based) is passed to the tune's init in the accumulator. The
    container header, if any, sets the SID model and the PAL/NTSC flag.
    """

    def __init__(self, song: Song, subtune: int = 0):
        self._song = song
        self._bus = None
        self._entries = {}
        self._layouts = {}
        self._lay: Optional[layout.Layout] = None
        self.layout: Optional[layout.Layout] = None
        sid = song.container_header[:4] in (PSID_MAGIC, RSID_MAGIC)
        header = parse_sid_header(song.container_header) if sid else None
        self._psid = header is None or header.is_psid
        self._hosted: Optional[InterruptPlayer] = None
        if header is not None and not song.play_addr:
            self._hosted = InterruptPlayer(song.container_header + song.image, subtune)
        super().__init__(
            song.image,
            song.load_addr,
            song.init_addr,
            song.play_addr or song.init_addr,
            subtune,
            max_cycles=MAX_CYCLES,
            sid_model=sid_model_for_flags(header.flags) if header else "6581",
            ntsc=bool(header and header.is_ntsc),
        )

    @property
    def base(self) -> int:
        """Native player base: the play entry sits at ``base + $21``."""
        return self.layout.entry - constants.NATIVE_PLAY_OFFSET

    @property
    def memory(self) -> bytes:
        """The 64 KiB the player runs in (after init, and every call since)."""
        return bytes(self._mem)

    @property
    def tempo_reload(self) -> int:
        """The play-call divider reload (``LDA #n`` at the play entry's BMI target)."""
        return self._rd(self.layout.tempo_reload)

    # -- memory helpers --------------------------------------------------
    def _set(self, offset: int, value: int) -> None:
        self._wr(self._lay.sid + offset, value)

    def _ptr(self, lo: int, hi: int, index: int) -> int:
        return self._rd(lo + index) | (self._rd(hi + index) << 8)

    def _inc(self, addr: int, delta: int) -> int:
        value = (self._rd(addr) + delta) & 0xFF
        self._wr(addr, value)
        return value

    def _rd(self, addr: int) -> int:
        return self._bus[addr & 0xFFFF]

    def _view(self) -> bytearray:
        """The 64 KiB as the CPU reads it under the current ``$01`` banking.

        A banked-in ROM page reads the ROM-less set (a 0xEA kernal, zero basic
        and chargen), not the RAM behind it; the RAM itself is left intact for
        a play routine that banks the ROM out before calling the player.
        """
        bus = self._bus
        view = bytearray(bus.ram)
        for page_id, page in enumerate(bus.pages):
            if page is not None and page is not bus.ram:
                span = slice(page_id << 12, (page_id + 1) << 12)
                view[span] = page[span]
        return view

    def _bank_entries(self) -> Set[int]:
        """The play entries the CPU sees under the current ``$01`` banking."""
        bits = self._bus.bits
        if bits not in self._entries:
            self._entries[bits] = {m.addr for m in layout.play_entries(self._view())}
        return self._entries[bits]

    # -- init --------------------------------------------------------------
    def _init(self, subtune: int) -> None:
        self._boot(subtune)
        self._bus = self._obs._subject
        play = self._song.play_addr
        lay = self._probe(play) if play else None
        if lay is None:
            entry = layout.resolve_entry(self._view(), play)
            if entry is None:
                raise MusicAssemblerParseError("no play entry after init")
            lay = self._instance(entry)
        self.layout = self._lay = lay

    def _boot(self, subtune: int) -> None:
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

    def _instance(self, entry: int) -> layout.Layout:
        """The layout of the player instance whose play entry is ``entry``."""
        key = (self._bus.bits, entry)
        if key not in self._layouts:
            self._layouts[key] = layout.discover(self._view(), entry)
        return self._layouts[key]

    # -- one play call -----------------------------------------------------
    def _frame(self) -> None:
        if self._hosted is not None:
            self._hosted.play_frame()
            return
        play = self._song.play_addr
        if play in (0, self.layout.entry):
            self._play(self.layout)
        else:
            self._call(play)

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

    def _probe(self, play: int) -> Optional[layout.Layout]:
        """The player instance the play routine calls first, leaving no trace."""
        mpu = self._mpu
        mem = bytes(self._mem)
        regs = (mpu.pc, mpu.sp, mpu.a, mpu.x, mpu.y, mpu.p, mpu.processorCycles)
        lay = self._call(play, probe=True)
        self._mem[:] = mem
        self._bus[1] = mem[1]
        mpu.pc, mpu.sp, mpu.a, mpu.x, mpu.y, mpu.p, mpu.processorCycles = regs
        return lay

    def _call(self, play: int, probe: bool = False) -> Optional[layout.Layout]:
        """Run the play routine on the CPU, modelling each call into the player.

        With ``probe`` stop at, and return the instance of, the first play entry
        reached (discovered under the banking the routine calls it with).
        """
        mpu, bus = self._mpu, self._obs
        top = mpu.sp
        for byte in (0x00, 0x01):
            bus[0x0100 + mpu.sp] = byte
            mpu.sp = (mpu.sp - 1) & 0xFF
        mpu.pc = play
        start = mpu.processorCycles
        while mpu.sp < top and mpu.processorCycles - start < MAX_CYCLES:
            if mpu.pc not in self._bank_entries():
                mpu.step()
                continue
            lay = self._instance(mpu.pc)
            if probe:
                return lay
            self._play(lay)
            mpu.sp = (mpu.sp + 2) & 0xFF
            ret = bus[0x00FF + mpu.sp] | (bus[0x0100 + mpu.sp] << 8)
            mpu.pc = (ret + 1) & 0xFFFF
        return None

    def _play(self, lay: layout.Layout) -> None:
        self._lay = lay
        if self._inc(lay.tempo, -1) < 0x80:
            for v in lay.output_voices:
                self._output(v)
            return
        self._wr(lay.tempo, self._rd(lay.tempo_reload))
        for v in range(constants.VOICES):
            if self._inc(lay.dur + v, -1) >= 0x80:
                self._parse(v)
            else:
                self._output(v)

    # -- pattern parse -----------------------------------------------------
    def _parse(self, v: int) -> None:
        lay = self._lay
        pid = self._rd(lay.pattern_id + v)
        if pid == constants.SILENCE_PATTERN:
            self._gate_off(v)
            return
        pat = self._ptr(lay.pattern_lo, lay.pattern_hi, pid)
        y = self._rd(lay.pat_cursor + v)
        val = self._rd(pat + y)
        if val >= constants.LONGDUR_MIN:
            self._wr(lay.dur + v, val & constants.CTL_DUR_MASK)
        elif val >= constants.INSTR_MIN:
            self._wr(lay.instr_x8 + v, val << 3)
            y = (y + 1) & 0xFF
            val = self._rd(pat + y)
            if val >= constants.DUR_MIN:
                self._duration(v, val)
            else:
                y = self._note(v, pat, y, val)
        elif val >= constants.DUR_MIN:
            self._duration(v, val)
        else:
            y = self._note(v, pat, y, val)
        self._row_end(v, pat, y)

    def _gate_off(self, v: int) -> None:
        addr = self._lay.ctrl + v
        self._wr(addr, self._rd(addr) & 0xFE)

    def _duration(self, v: int, val: int) -> None:
        lay = self._lay
        self._wr(lay.dur + v, val & constants.CTL_DUR_MASK)
        self._wr(lay.ctrl_mask + v, 0xFE)
        self._gate_off(v)

    def _note(self, v: int, pat: int, y: int, note: int) -> int:
        lay = self._lay
        idx = ((self._rd(lay.transrep + v) >> 4) + note) & 0xFF
        self._wr(lay.note_index + v, idx)
        for table, work, vib in (
            (lay.freq_lo, lay.freq_lo_w, lay.vib_freq_lo),
            (lay.freq_hi, lay.freq_hi_w, lay.vib_freq_hi),
        ):
            self._wr(work + v, self._rd(table + idx))
            self._wr(vib + v, self._rd(table + idx))
        y = (y + 1) & 0xFF
        ctl = self._rd(pat + y)
        self._wr(lay.note_ctl + v, ctl)
        self._wr(lay.dur + v, ctl & constants.CTL_DUR_MASK)
        if ctl & constants.CTL_FILTER:
            self._wr(lay.filter_owner, v)
            y = (y + 1) & 0xFF
            param = self._rd(pat + y)
            self._wr(lay.fc_acc_reset, param)
            self._wr(lay.fc_step, ((param & 0x0F) << 1) - 0x10)
            y = (y + 1) & 0xFF
            res = self._rd(pat + y)
            if res == 0:
                self._set(RES_FILT, 0xF0)
            else:
                self._wr(lay.fc_ctr_reset, res)
                self._set(RES_FILT, self._rd(lay.res_presets + v))
        elif ctl & constants.CTL_VIBARP:
            y = (y + 1) & 0xFF
            self._wr(lay.vib_lo_step + v, self._rd(pat + y))
            y = (y + 1) & 0xFF
            self._wr(lay.vib_hi_step + v, self._rd(pat + y))
        self._wr(lay.ctrl_mask + v, 0xFF)
        self._wr(lay.vib_toggle + v, 0xFF)
        self._wr(lay.arp_substep + v, 0)
        self._wr(lay.arp_phase + v, 0)
        return y

    def _row_end(self, v: int, pat: int, y: int) -> None:
        lay = self._lay
        y = (y + 1) & 0xFF
        if self._rd(pat + y) != constants.PATTERN_END:
            self._wr(lay.pat_cursor + v, y)
            return
        self._wr(lay.pat_cursor + v, 0)
        if self._inc(lay.repeat + v, -1) < 0x80:
            return
        order = self._ptr(lay.order_lo, lay.order_hi, v)
        y = (self._rd(lay.order_cursor + v) + 2) & 0xFF
        while True:
            val = self._rd(order + y)
            if lay.order_tempo and val == constants.ORDER_TEMPO:
                tempo = self._rd(order + ((y + 1) & 0xFF))
                self._wr(lay.tempo_reload, tempo)
                self._wr(lay.tempo, tempo)
                y = (y + 2) & 0xFF
            elif lay.order_jump and val == constants.ORDER_JUMP:
                y = (self._rd(order + ((y + 1) & 0xFF)) << 1) & 0xFF
            else:
                break
        if val == constants.ORDER_END:
            y = 0
        self._wr(lay.order_cursor + v, y)
        self._wr(lay.pattern_id + v, self._rd(order + y))
        transrep = self._rd(order + ((y + 1) & 0xFF))
        self._wr(lay.transrep_byte + v, transrep)
        self._wr(lay.repeat + v, transrep & 0x0F)

    # -- per-call output -----------------------------------------------------
    def _output(self, v: int) -> None:
        lay = self._lay
        x8 = self._rd(lay.instr_x8 + v)
        rec = lay.instr + x8
        ctl = self._rd(lay.note_ctl + v)
        if not ctl & constants.CTL_TRIGGERED:
            self._trigger(v, rec, ctl)
        else:
            self._sustain(v, rec, ctl)
        self._sid_final(v)

    def _trigger(self, v: int, rec: int, ctl: int) -> None:
        lay = self._lay
        ofs = self._rd(lay.voice_reg + v)
        self._wr(lay.instr_cursor + v, 0)
        self._set(ofs + SR, self._rd(rec + constants.INSTR_SR))
        self._set(ofs + AD, self._rd(rec + constants.INSTR_AD))
        self._set(ofs + CTRL, self._rd(lay.ctrl + v) & 0xFE)
        self._wr(lay.ctrl + v, self._rd(rec + constants.INSTR_WAVEFORM))
        pulse = self._rd(rec + constants.INSTR_PULSE_INIT)
        self._wr(lay.pw_lo + v, pulse)
        self._wr(lay.pw_hi + v, pulse)
        if v == self._rd(lay.trigger_owner):
            self._wr(lay.fc_acc, self._rd(lay.trigger_acc))
            self._wr(lay.fc_ctr, self._rd(lay.trigger_ctr))
        self._wr(lay.pw_substep + v, 0)
        self._wr(lay.pw_dir + v, 0)
        self._wr(lay.porta_ctr + v, self._rd(rec + constants.INSTR_ARP_SPEED) >> 3)
        self._wr(lay.note_ctl + v, ctl | constants.CTL_TRIGGERED)
        self._wr(lay.flags + v, self._rd(rec + constants.INSTR_PORTA))

    def _sustain(self, v: int, rec: int, ctl: int) -> None:
        lay = self._lay
        if v == self._rd(lay.sweep_owner) and self._rd(lay.sweep_ctr):
            self._inc(lay.sweep_dec, -1)
            cutoff = self._rd(lay.sweep_acc) + self._rd(lay.sweep_step)
            self._wr(lay.sweep_sta, cutoff)
            self._set(FC_HI, cutoff & 0xFF)
        flags = self._rd(lay.flags + v)
        if flags & 0x0F:
            self._program(v, flags & 0x0F)
        elif not ctl & constants.CTL_VIBARP and flags & 0x10:
            self._arp(v, rec)
        self._pulse(v, rec, self._rd(lay.flags + v))

    def _arp(self, v: int, rec: int) -> None:
        lay = self._lay
        if self._inc(lay.porta_ctr + v, -1) < 0x80:
            return
        self._inc(lay.porta_ctr + v, 1)
        step = self._rd(rec + constants.INSTR_ARP_STEP)
        lo, hi = lay.freq_lo_w + v, lay.freq_hi_w + v
        ascend = self._rd(lay.arp + (self._rd(lay.arp_phase + v) & 3))
        if not ascend:
            borrow = self._rd(lo) < step
            self._inc(lo, -step)
            ascend = borrow and self._inc(hi, -1) == 0
        if ascend:
            carry = self._rd(lo) + step > 0xFF
            self._inc(lo, step)
            if carry:
                self._inc(hi, 1)
        if self._inc(lay.arp_substep + v, 1) == (
            self._rd(rec + constants.INSTR_ARP_SPEED) & 0x0F
        ):
            self._wr(lay.arp_substep + v, 0)
            self._inc(lay.arp_phase + v, 1)

    def _program(self, v: int, prog: int) -> None:
        lay = self._lay
        base = self._ptr(lay.inner_lo, lay.inner_hi, prog)
        cur = self._rd(lay.instr_cursor + v)
        self._wr(lay.ctrl + v, self._rd(base + cur) & self._rd(lay.ctrl_mask + v))
        pitch = self._rd(base + ((cur + 1) & 0xFF))
        if pitch < 0x80 or not lay.pitch_skip:
            pitch += self._rd(lay.note_index + v)
        pitch &= 0x7F
        cutoff = self._rd(base + ((cur + 2) & 0xFF))
        if cutoff:
            self._wr(lay.program_acc, cutoff)
        nxt = (cur + 3) & 0xFF
        marker = self._rd(base + nxt)
        if lay.program_jump and marker == layout.PROGRAM_JUMP:
            row = self._rd(base + ((nxt + 1) & 0xFF))
            bound = (self._rd(lay.program_jump + v) + 3) & 0xFF
            nxt = 0 if row == 0 or row >= bound else (row * 3) & 0xFF
        elif marker == 0xFE:
            self._wr(lay.flags + v, self._rd(lay.flags + v) & 0xF0)
        elif marker == 0xFF:
            nxt = 0
        self._wr(lay.instr_cursor + v, nxt)
        self._wr(lay.freq_lo_w + v, self._rd(lay.freq_lo + pitch))
        self._wr(lay.freq_hi_w + v, self._rd(lay.freq_hi + pitch))

    def _pulse(self, v: int, rec: int, flags: int) -> None:
        lay = self._lay
        speed = self._rd(rec + constants.INSTR_PULSE_STEP)
        lo, hi = lay.pw_lo + v, lay.pw_hi + v
        if flags & 0x40 or lay.pulse_always:
            carry = (self._rd(lo) + speed) >> 8
            self._inc(lo, speed)
            self._inc(hi, speed + carry)
        elif flags & 0x20:
            if self._rd(lay.pw_dir + v):
                if self._rd(lo) + speed > 0xFF:
                    self._inc(hi, 1)
                self._inc(lo, speed)
            else:
                if self._rd(lo) < speed:
                    self._inc(hi, -1)
                self._inc(lo, -speed)
            if self._inc(lay.pw_substep + v, 1) == speed & 0x0F:
                self._wr(lay.pw_substep + v, 0)
                self._wr(lay.pw_dir + v, self._rd(lay.pw_dir + v) ^ 1)

    def _sid_final(self, v: int) -> None:
        lay = self._lay
        ofs = self._rd(lay.voice_reg + v)
        self._set(ofs + CTRL, self._rd(lay.ctrl + v))
        self._set(ofs + PW_HI, self._rd(lay.pw_hi + v))
        self._set(ofs + PW_LO, self._rd(lay.pw_lo + v))
        if self._rd(lay.note_ctl + v) & constants.CTL_VIBARP:
            toggled = 0
            if self._rd(lay.vib_lo_step + v) & 1:
                toggled = self._rd(lay.vib_toggle + v) ^ 0xFF
                self._wr(lay.vib_toggle + v, toggled)
            if not toggled:
                lo_step = self._rd(lay.vib_lo_step + v)
                carry = (self._rd(lay.vib_freq_lo + v) + lo_step) >> 8
                self._set(ofs + FREQ_LO, self._inc(lay.vib_freq_lo + v, lo_step))
                hi_step = self._rd(lay.vib_hi_step + v) + carry
                self._set(ofs + FREQ_HI, self._inc(lay.vib_freq_hi + v, hi_step))
                return
        self._set(ofs + FREQ_LO, self._rd(lay.freq_lo_w + v))
        self._set(ofs + FREQ_HI, self._rd(lay.freq_hi_w + v))


def iter_frames(song: Song, max_frames: Optional[int] = None):
    """Yield per-call register write lists for ``song`` (see :class:`MemPlayer`)."""
    return Player(song).iter_frames(max_frames)


def render_grid(song: Song, nframes: int) -> List[List[int]]:
    """Render ``nframes`` forward-filled per-call register snapshots."""
    return Player(song).render_grid(nframes)
