"""Music Assembler playroutine as a :class:`pysidtracker.MemPlayer`.

The tune's own init runs in the 6502 emulator (selecting the subtune and
placing any relocated data); the play call is then modelled in Python on the
player's work cells, at the addresses :mod:`pymusicassembler.layout` finds.

The start-up memory is the shared libsidplayfp power-on image
(:func:`pysidtracker.power_on_ram`), inherited from :class:`MemPlayer`, so a
record read past the end of the image sees what the C64 RAM held at power-on.
"""

from typing import List, Optional

from pysidtracker import (
    MemPlayer,
    psid_init_bank,
    psid_play_bank,
    run_to_rts,
    wire_mpu,
)
from pysidtracker.registers import AD, CTRL, FC_HI, FREQ_HI, FREQ_LO, PW_HI, PW_LO
from pysidtracker.registers import PW_HI_REGS, RES_FILT, SR

from pymusicassembler import constants, layout
from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.model import Song

MAX_CYCLES = 8_000_000


class Player(MemPlayer):
    """Play a :class:`~pymusicassembler.model.Song` one play call at a time.

    ``subtune`` (0-based) is passed to the tune's init in the accumulator.
    """

    def __init__(self, song: Song, subtune: int = 0):
        self._song = song
        self._mpu = self._bus = None
        self._entries = set()
        self._layouts = {}
        self._lay: Optional[layout.Layout] = None
        self.layout: Optional[layout.Layout] = None
        super().__init__(song.image, song.load_addr, subtune)

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

    def snapshot(self) -> List[int]:
        """The SID register file, pulse-width highs masked to the nibble the SID uses."""
        regs = super().snapshot()
        for reg in PW_HI_REGS:
            regs[reg] &= 0x0F
        return regs

    # -- memory helpers --------------------------------------------------
    def _set(self, offset: int, value: int) -> None:
        self._wr(self._lay.sid + offset, value)

    def _ptr(self, lo: int, hi: int, index: int) -> int:
        return self._rd(lo + index) | (self._rd(hi + index) << 8)

    def _inc(self, addr: int, delta: int) -> int:
        value = (self._rd(addr) + delta) & 0xFF
        self._wr(addr, value)
        return value

    def _view_rom_pages(self) -> None:
        """Make the flat memory read what the bus does for a banked-in ROM page.

        With a ROM banked in over its RAM (the PSID driver's own iomap), the C64
        reads the ROM-less set -- a 0xEA kernal, zero basic and chargen -- not the
        RAM behind it, so a player table that lands in those banks sees it too.
        """
        bus = self._bus._subject
        for page_id, page in enumerate(bus.pages):
            if page is None or page is bus.ram:
                continue
            self._mem[page_id << 12 : (page_id + 1) << 12] = page[
                page_id << 12 : (page_id + 1) << 12
            ]

    # -- init --------------------------------------------------------------
    def _init(self, subtune: int) -> None:
        # Boot the bus as the PSID driver does and leave the play bank in.
        self._mem[1] = psid_init_bank(self._song.init_addr)
        self._mpu, self._bus = wire_mpu(self._mem)
        run_to_rts(self._mpu, self._bus, self._song.init_addr, subtune, MAX_CYCLES)
        # Bank the play's ROM space in through the bus, which re-derives its pages.
        self._bus[1] = psid_play_bank(self._song.play_addr or self._song.init_addr)
        self._view_rom_pages()
        self._entries = {m.addr for m in layout.play_entries(self._mem)}
        play = self._song.play_addr
        entry = self._probe(play) if play else None
        if entry is None:
            entry = layout.resolve_entry(self._mem, play)
        if entry is None:
            raise MusicAssemblerParseError("no play entry after init")
        self.layout = self._lay = self._instance(entry)

    def _instance(self, entry: int) -> layout.Layout:
        """The layout of the player instance whose play entry is ``entry``."""
        if entry not in self._layouts:
            self._layouts[entry] = layout.discover(self._mem, entry)
        return self._layouts[entry]

    # -- one play call -----------------------------------------------------
    def _frame(self) -> None:
        play = self._song.play_addr
        if play in (0, self.layout.entry):
            self._play(self.layout)
        else:
            self._call(play)

    def _probe(self, play: int) -> Optional[int]:
        """The play entry the play routine reaches first, leaving no trace."""
        mpu = self._mpu
        mem = bytes(self._mem)
        regs = (mpu.pc, mpu.sp, mpu.a, mpu.x, mpu.y, mpu.p, mpu.processorCycles)
        entry = self._call(play, probe=True)
        self._mem[:] = mem
        mpu.pc, mpu.sp, mpu.a, mpu.x, mpu.y, mpu.p, mpu.processorCycles = regs
        return entry

    def _call(self, play: int, probe: bool = False) -> Optional[int]:
        """Run the play routine on the CPU, modelling each call into the player.

        With ``probe`` stop at, and return, the first play entry reached.
        """
        mpu, bus = self._mpu, self._bus
        top = mpu.sp
        for byte in (0x00, 0x01):
            bus[0x0100 + mpu.sp] = byte
            mpu.sp = (mpu.sp - 1) & 0xFF
        mpu.pc = play
        start = mpu.processorCycles
        while mpu.sp < top and mpu.processorCycles - start < MAX_CYCLES:
            if mpu.pc not in self._entries:
                mpu.step()
                continue
            if probe:
                return mpu.pc
            self._play(self._instance(mpu.pc))
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
            nxt = 0 if row == 0 or row >= nxt else (row * 3) & 0xFF
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
        if flags & 0x40:
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
