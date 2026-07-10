"""Music Assembler playroutine in Python.

A faithful, byte-exact reimplementation of the Music Assembler player's
per-frame DSP, recovered from the player's disassembly
(``decompile.c``) and validated byte-exact against the
``preframr-sidtrace`` register oracle.  The control flow -- tempo
divider, orderlist + pattern walk, the 16-bit pulse-width sweep, the
vibrato/arp frequency accumulators, the gate/retrigger idiom and the
filter-cutoff sweep -- is the player's own algorithm; the per-tune
ADDRESSES it operates on are discovered by the reader, so a relocated
tune of the same player plays unchanged.

Each :meth:`Player.play_frame` call runs one frame and returns the SID
register writes for that frame as ``(register, value)`` pairs in
ascending register order (changed registers only, after the first
frame), mirroring :class:`pymusicassembler.model.Song`'s output.
"""

from dataclasses import dataclass
from typing import List, Optional

from pysidtracker.registers import (
    AD,
    CTRL,
    FC_HI,
    FREQ_HI,
    FREQ_LO,
    MODE_VOL,
    PW_HI,
    PW_LO,
    RES_FILT,
    SR,
)

from pymusicassembler import constants, reader
from pymusicassembler.model import Song

SID_OFFSET = constants.SID_OFFSET


@dataclass
class _Voice:
    """Per-voice work state (mirrors the player's ``$..,X`` work arrays)."""

    # pylint: disable=too-many-instance-attributes  # mirrors the player state
    pat_cursor: int = 0  # $1081
    ctrl: int = 0  # $1084 waveform/CTRL
    order_cursor: int = 0  # $1087 orderlist cursor
    dur: int = 0  # $108a note-duration counter
    pattern_id: int = 0  # $108d
    note_index: int = 0  # $10c9
    freq_lo: int = 0  # $10cc plain FREQ_LO
    freq_hi: int = 0  # $10cf plain FREQ_HI
    arp_substep: int = 0  # $10dd
    pw_substep: int = 0  # $10e0
    pw_dir: int = 0  # $10e3
    transrep: int = 0  # $10e6 transpose(hi)/repeat(lo)
    repeat_ctr: int = 0  # $10e9
    ctrl_mask: int = 0xFF  # $1031 gate/CTRL AND-mask
    note_ctl: int = 0  # $1141 note control byte
    instr_cursor: int = 0  # $1144
    vib_lo_step: int = 0  # $1147
    vib_hi_step: int = 0  # $114a
    porta_ctr: int = 0  # $114d
    vib_freq_hi: int = 0  # $12b6 vibrato FREQ_HI accumulator
    vib_toggle: int = 0  # $12b9
    arp_phase: int = 0  # $12bd
    instr_x8: int = 0  # $13d9 instrument id * 8 (record byte offset)
    pw_lo: int = 0  # $13dc PW_LO accumulator
    pw_hi: int = 0  # $13df PW_HI accumulator
    vib_freq_lo: int = 0  # $13e2 vibrato FREQ_LO accumulator
    flags: int = 0  # $00fd effect flags


@dataclass
class _Tables:
    """Resolved per-tune table base addresses (absolute C64 addresses)."""

    # pylint: disable=too-many-instance-attributes  # one field per table base
    order_lo: int
    order_hi: int
    pattern_lo: int
    pattern_hi: int
    instr: int
    freq_lo: int
    freq_hi: int
    inner_lo: int
    inner_hi: int
    arp: int


class Player:
    """Play a :class:`~pymusicassembler.model.Song` one frame at a time."""

    # pylint: disable=too-many-instance-attributes  # full player machine state
    def __init__(self, song: Song):
        self._song = song
        self._image = song.image
        self._load = song.load_addr
        self._tables = _resolve_tables(song)
        self.regs = [0] * constants.SID_REGISTERS
        self._voices = [_Voice() for _ in range(constants.VOICES)]
        self._tempo = 0  # $1090 (init 0; underflows on frame 0 -> advance)
        self._filter_owner = 0  # $1262
        self._fc_acc = 0  # $129e accumulator (self-modified LDA immediate)
        self._fc_ctr = 0  # $1296 gate counter (self-modified LDA immediate)
        self._fc_step = 0  # $12a0 sweep step (self-modified ADC immediate)
        self._last_regs: Optional[List[int]] = None
        self._init()

    # -- raw image access ------------------------------------------------
    def _byte(self, abs_addr: int) -> int:
        idx = abs_addr - self._load
        if 0 <= idx < len(self._image):
            return self._image[idx]
        return 0

    def _orderlist_addr(self, voice: int) -> int:
        tab = self._tables
        return self._byte(tab.order_lo + voice) | (
            self._byte(tab.order_hi + voice) << 8
        )

    def _pattern_addr(self, pattern_id: int) -> int:
        tab = self._tables
        lo = self._byte(tab.pattern_lo + pattern_id)
        hi = self._byte(tab.pattern_hi + pattern_id)
        return lo | (hi << 8)

    def _inner_addr(self, flags: int) -> int:
        tab = self._tables
        return self._byte(tab.inner_lo + flags) | (
            self._byte(tab.inner_hi + flags) << 8
        )

    def _instr_byte(self, idx: int, byte_off: int) -> int:
        return self._byte(
            self._tables.instr + idx * constants.INSTR_RECORD_SIZE + byte_off
        )

    def _freq_lo(self, note_index: int) -> int:
        return self._byte(self._tables.freq_lo + note_index)

    def _freq_hi(self, note_index: int) -> int:
        return self._byte(self._tables.freq_hi + note_index)

    def _res_preset(self, v: int) -> int:
        return constants.RES_PRESETS[v]

    # -- init ($1048) ----------------------------------------------------
    def _init(self) -> None:
        self.regs[MODE_VOL] = 0x1F
        self.regs[RES_FILT] = 0xF0
        # The init routine zeroes only $1081..$1090 (pat_cursor / ctrl /
        # order_cursor / dur / pattern_id) and the filter-owner flag; every
        # other work-RAM byte keeps its loaded-image value (a tune saved
        # mid-playback bakes live state in).  Seed those from the image so the
        # player's first frames match the chip's, then apply the init reloads.
        # The filter-sweep state ($1262 owner, $129e acc, $1296 gate, $12a0
        # step) is self-modified player code; init does not clear it, so it
        # keeps the loaded-image immediates until a filter command / voice-0
        # trigger overwrites them.
        self._filter_owner = self._byte(constants.WORK_FILTER_OWNER)
        self._fc_acc = self._byte(0x129E)
        self._fc_ctr = self._byte(0x1296)
        self._fc_step = self._byte(0x12A0)
        # Voice-0 trigger reset values ($1265/$126a immediates) -- per-tune
        # constants baked into the player code region (the player customises
        # its filter-sweep envelope per tune).
        self._fc_acc_reset = self._byte(0x1266)
        self._fc_ctr_reset = self._byte(0x126B)
        self._tempo = 0  # $1090 cleared by the init loop
        for v, voice in enumerate(self._voices):
            voice.pat_cursor = 0
            voice.ctrl = 0
            voice.order_cursor = 0
            voice.dur = 0
            voice.note_index = self._byte(constants.WORK_NOTE_INDEX + v)
            voice.freq_lo = self._byte(constants.WORK_FREQ_LO + v)
            voice.freq_hi = self._byte(constants.WORK_FREQ_HI + v)
            voice.arp_substep = self._byte(constants.WORK_ARP_SUBSTEP + v)
            voice.pw_substep = self._byte(constants.WORK_PW_SUBSTEP + v)
            voice.pw_dir = self._byte(constants.WORK_PW_DIR + v)
            voice.note_ctl = self._byte(constants.WORK_NOTE_CTL + v)
            voice.instr_cursor = self._byte(constants.WORK_INSTR_CURSOR + v)
            voice.vib_lo_step = self._byte(constants.WORK_VIB_LO_STEP + v)
            voice.vib_hi_step = self._byte(constants.WORK_VIB_HI_STEP + v)
            voice.porta_ctr = self._byte(constants.WORK_PORTA_CTR + v)
            voice.vib_freq_hi = self._byte(constants.WORK_VIB_FREQ_HI + v)
            voice.vib_toggle = self._byte(constants.WORK_VIB_TOGGLE + v)
            voice.arp_phase = self._byte(constants.WORK_ARP_PHASE + v)
            voice.instr_x8 = self._byte(constants.WORK_INSTR_X8 + v)
            voice.pw_lo = self._byte(constants.WORK_PW_LO + v)
            voice.pw_hi = self._byte(constants.WORK_PW_HI + v)
            voice.vib_freq_lo = self._byte(constants.WORK_VIB_FREQ_LO + v)
            voice.ctrl_mask = self._byte(constants.WORK_CTRL_MASK + v)
            voice.flags = self._byte(constants.WORK_FLAGS + v)
            # init reload: pattern_id / transrep / repeat from the orderlist.
            order = self._orderlist_addr(v)
            voice.pattern_id = self._byte(order)
            second = self._byte(order + 1)
            voice.transrep = second
            voice.repeat_ctr = second & 0x0F

    # -- one play call ($1021) ------------------------------------------
    def play_frame(self) -> List[tuple]:
        """Run one player tick; return this frame's SID register writes.

        The first frame returns all 25 registers; later frames return
        only registers whose value changed.
        """
        self._frame()
        if self._last_regs is None:
            writes = list(enumerate(self.regs))
        else:
            writes = [
                (reg, value)
                for reg, (value, last) in enumerate(zip(self.regs, self._last_regs))
                if value != last
            ]
        self._last_regs = list(self.regs)
        return writes

    def _frame(self) -> None:
        self._tempo = (self._tempo - 1) & 0xFF
        advance = self._tempo >= 0x80  # signed < 0
        if advance:
            self._tempo = constants.TEMPO_DIVIDER
        for v in range(constants.VOICES):
            voice = self._voices[v]
            if not advance:
                self._output(v)
                continue
            voice.dur = (voice.dur - 1) & 0xFF
            if voice.dur >= 0x80:  # signed < 0 -> new row
                self._advance_row(v)
            else:
                self._output(v)

    # -- orderlist + pattern parse ($1091) ------------------------------
    def _advance_row(self, v: int) -> None:
        voice = self._voices[v]
        if voice.pattern_id == constants.SILENCE_PATTERN:
            voice.ctrl &= 0xFE
            return
        pat = self._pattern_addr(voice.pattern_id)
        cursor = voice.pat_cursor
        val = self._byte(pat + cursor)
        if val >= constants.INSTR_MIN:
            if val >= constants.LONGDUR_MIN:  # long-duration token
                voice.dur = val & constants.CTL_DUR_MASK
                self._after_row(v, pat, cursor)
                return
            # instrument select: id*8 kept in one byte, ASL wraps at 8 bits.
            voice.instr_x8 = (val << 3) & 0xFF
            cursor += 1
            val = self._byte(pat + cursor)
            if val >= constants.DUR_MIN:
                voice.dur = val & constants.CTL_DUR_MASK
                self._after_row(v, pat, cursor)
                return
            # else fall to note
        elif val >= constants.DUR_MIN:  # bare duration token ($60..$7F)
            # $10b6: dur = val & 0x1f, gate-off mask, gate off, then advance
            # the cursor / check the pattern end (the $10c3 JMP $1187).
            voice.dur = val & constants.CTL_DUR_MASK
            voice.ctrl_mask = 0xFE
            voice.ctrl &= 0xFE
            self._after_row(v, pat, cursor)
            return
        self._parse_note(v, pat, cursor, val)

    def _parse_note(self, v: int, pat: int, cursor: int, note: int) -> None:
        voice = self._voices[v]
        idx = (voice.transrep >> 4) + note
        voice.note_index = idx & 0xFF
        voice.freq_lo = self._freq_lo(voice.note_index)
        voice.vib_freq_lo = voice.freq_lo
        voice.freq_hi = self._freq_hi(voice.note_index)
        voice.vib_freq_hi = voice.freq_hi
        cursor += 1
        ctl = self._byte(pat + cursor)
        voice.note_ctl = ctl
        voice.dur = ctl & constants.CTL_DUR_MASK
        if ctl >= constants.CTL_FILTER:  # filter command attached ($1150)
            self._filter_owner = v
            cursor += 1
            param = self._byte(pat + cursor)
            # step = (param & 0xf) * 2 - 0x10  (self-modified ADC immediate).
            self._fc_step = (((param & 0x0F) << 1) - 0x10) & 0xFF
            cursor += 1
            third = self._byte(pat + cursor)
            if third == 0:
                self.regs[RES_FILT] = 0xF0
            else:
                self.regs[RES_FILT] = self._res_preset(v)
        elif ctl & constants.CTL_VIBARP:  # vibrato/arp params attached
            cursor += 1
            voice.vib_lo_step = self._byte(pat + cursor)
            cursor += 1
            voice.vib_hi_step = self._byte(pat + cursor)
        voice.ctrl_mask = 0xFF
        voice.vib_toggle = 0xFF
        voice.arp_substep = 0
        voice.arp_phase = 0
        self._after_row(v, pat, cursor)

    def _after_row(self, v: int, pat: int, cursor: int) -> None:
        voice = self._voices[v]
        cursor += 1
        nb = self._byte(pat + cursor)
        if nb == constants.PATTERN_END:
            voice.repeat_ctr = (voice.repeat_ctr - 1) & 0xFF
            if voice.repeat_ctr < 0x80:  # signed >= 0: replay pattern
                voice.pat_cursor = 0
                return
            order = self._orderlist_addr(v)
            ocur = voice.order_cursor + 2
            if self._byte(order + ocur) == constants.ORDER_END:
                ocur = 0
            voice.order_cursor = ocur
            voice.pattern_id = self._byte(order + ocur)
            voice.transrep = self._byte(order + ocur + 1)
            voice.repeat_ctr = voice.transrep & 0x0F
            voice.pat_cursor = 0
        else:
            voice.pat_cursor = cursor

    # -- per-frame output + generators ----------------------------------
    def _output(self, v: int) -> None:
        voice = self._voices[v]
        ofs = SID_OFFSET[v]
        triggered = voice.note_ctl & constants.CTL_TRIGGERED
        if not triggered:  # NOTE-TRIGGER frame
            voice.instr_cursor = 0
            idx = voice.instr_x8 >> 3
            self.regs[ofs + SR] = self._instr_byte(idx, constants.INSTR_SR)
            self.regs[ofs + AD] = self._instr_byte(idx, constants.INSTR_AD)
            self.regs[ofs + CTRL] = voice.ctrl & 0xFE
            voice.ctrl = self._instr_byte(idx, constants.INSTR_WAVEFORM)
            pw_init = self._instr_byte(idx, constants.INSTR_PULSE_INIT)
            voice.pw_lo = pw_init
            voice.pw_hi = pw_init
            if v == 0:
                self._fc_acc = self._fc_acc_reset
                self._fc_ctr = self._fc_ctr_reset
            voice.pw_substep = 0
            voice.pw_dir = 0
            voice.porta_ctr = self._instr_byte(idx, constants.INSTR_ARP_SPEED) >> 3
            voice.note_ctl |= constants.CTL_TRIGGERED
            voice.flags = self._instr_byte(idx, constants.INSTR_PORTA)
            self._sid_final(v)
            return
        # SUSTAIN frame.  Filter-cutoff sweep ($1290): only the owner voice
        # runs it; while the gate counter ($1296) is nonzero, decrement it,
        # add the step ($12a0) to the accumulator ($129e) and write FC_HI.
        if v == self._filter_owner and self._fc_ctr != 0:
            self._fc_ctr = (self._fc_ctr - 1) & 0xFF
            self._fc_acc = (self._fc_acc + self._fc_step) & 0xFF
            self.regs[FC_HI] = self._fc_acc
        flags = voice.flags & 0x0F
        if flags != 0:
            self._instrument_tick(v, flags)
        elif (voice.note_ctl & constants.CTL_VIBARP) == 0 and (voice.flags & 0x10) != 0:
            self._arp_walk(v)
        self._pw_gen(v)
        self._sid_final(v)

    def _arp_walk(self, v: int) -> None:
        voice = self._voices[v]
        voice.porta_ctr = (voice.porta_ctr - 1) & 0xFF
        if voice.porta_ctr < 0x80:  # signed >= 0: not yet
            return
        voice.porta_ctr = (voice.porta_ctr + 1) & 0xFF
        idx = voice.instr_x8 >> 3
        step = self._instr_byte(idx, constants.INSTR_ARP_STEP)
        phase = voice.arp_phase & 3
        ascend = self._byte(self._tables.arp + phase)
        if ascend == 0:  # descending
            lo = voice.freq_lo - step
            voice.freq_lo = lo & 0xFF
            if lo < 0:
                voice.freq_hi = (voice.freq_hi - 1) & 0xFF
        else:  # ascending
            lo = voice.freq_lo + step
            voice.freq_lo = lo & 0xFF
            if lo > 0xFF:
                voice.freq_hi = (voice.freq_hi + 1) & 0xFF
        voice.arp_substep = (voice.arp_substep + 1) & 0xFF
        arpspeed = self._instr_byte(idx, constants.INSTR_ARP_SPEED) & 0x0F
        if arpspeed == voice.arp_substep:
            voice.arp_substep = 0
            voice.arp_phase = (voice.arp_phase + 1) & 0xFF

    def _instrument_tick(self, v: int, flags: int) -> None:
        voice = self._voices[v]
        base = self._inner_addr(flags)
        cur = voice.instr_cursor
        ctrl = self._byte(base + cur) & voice.ctrl_mask
        voice.ctrl = ctrl
        pitch = self._byte(base + cur + 1)
        if pitch < 0x80:
            pitch = (pitch + voice.note_index) & 0xFF
        pitch &= 0x7F
        cutoff = self._byte(base + cur + 2)
        if cutoff != 0:
            self._fc_acc = cutoff
        marker = self._byte(base + cur + 3)
        if marker >= 0xFE:
            if marker == 0xFE:  # stop the effect
                voice.flags &= 0xF0
                cur = cur + 3
            else:  # $FF: loop to start
                cur = 0
        else:
            cur = cur + 3
        voice.instr_cursor = cur
        voice.freq_lo = self._freq_lo(pitch)
        voice.freq_hi = self._freq_hi(pitch)

    def _pw_gen(self, v: int) -> None:
        voice = self._voices[v]
        idx = voice.instr_x8 >> 3
        speed = self._instr_byte(idx, constants.INSTR_PULSE_STEP)
        flags = voice.flags
        if flags & 0x40:  # linear add
            lo = voice.pw_lo + speed
            voice.pw_lo = lo & 0xFF
            voice.pw_hi = (voice.pw_hi + speed + (lo >> 8)) & 0xFF
        elif flags & 0x20:  # triangle up/down
            if voice.pw_dir:
                lo = voice.pw_lo + speed
                voice.pw_lo = lo & 0xFF
                if lo > 0xFF:
                    voice.pw_hi = (voice.pw_hi + 1) & 0xFF
            else:
                lo = voice.pw_lo - speed
                voice.pw_lo = lo & 0xFF
                if lo < 0:
                    voice.pw_hi = (voice.pw_hi - 1) & 0xFF
            voice.pw_substep = (voice.pw_substep + 1) & 0xFF
            if (speed & 0x0F) == voice.pw_substep:
                voice.pw_substep = 0
                voice.pw_dir ^= 1

    def _sid_final(self, v: int) -> None:
        voice = self._voices[v]
        ofs = SID_OFFSET[v]
        self.regs[ofs + CTRL] = voice.ctrl
        self.regs[ofs + PW_HI] = voice.pw_hi & 0x0F
        self.regs[ofs + PW_LO] = voice.pw_lo
        if voice.note_ctl & constants.CTL_VIBARP:  # vibrato/arp-freq mode
            if voice.vib_lo_step & 1:
                voice.vib_toggle ^= 0xFF
                if voice.vib_toggle != 0:
                    self.regs[ofs + FREQ_LO] = voice.freq_lo
                    self.regs[ofs + FREQ_HI] = voice.freq_hi
                    return
            lo = voice.vib_freq_lo + voice.vib_lo_step
            voice.vib_freq_lo = lo & 0xFF
            self.regs[ofs + FREQ_LO] = voice.vib_freq_lo
            voice.vib_freq_hi = (
                voice.vib_freq_hi + voice.vib_hi_step + (lo >> 8)
            ) & 0xFF
            self.regs[ofs + FREQ_HI] = voice.vib_freq_hi
            return
        self.regs[ofs + FREQ_LO] = voice.freq_lo
        self.regs[ofs + FREQ_HI] = voice.freq_hi


def _resolve_tables(song: Song) -> _Tables:
    """Resolve the per-tune table base addresses from the song's image.

    The Music Assembler player binary is identical across tunes, so each
    pointer-table base lives at a fixed offset *within the player code* as a
    16-bit operand.  The ``OP_*`` offsets are measured from the play routine
    (base+$21); an image whose bytes begin before it -- e.g. a native song
    with the self-start stub at its base, or any rip that keeps the header --
    shifts every operand by that much, so anchor on the discovered player
    base rather than assuming the play routine is at image offset 0.
    """
    image = song.image
    view = reader.load_view(image, song.load_addr)
    base = reader.find_player_base(view, song.load_addr, len(image))
    code_off = (
        base + constants.NATIVE_PLAY_OFFSET - song.load_addr if base is not None else 0
    )

    def op16(off: int) -> int:
        off += code_off
        if off + 1 < len(image):
            return image[off] | (image[off + 1] << 8)
        return 0

    return _Tables(
        order_lo=op16(constants.OP_ORDER_LO),
        order_hi=op16(constants.OP_ORDER_HI),
        pattern_lo=op16(constants.OP_PATTERN_LO),
        pattern_hi=op16(constants.OP_PATTERN_HI),
        instr=op16(constants.OP_INSTR),
        freq_lo=op16(constants.OP_FREQ_LO),
        freq_hi=op16(constants.OP_FREQ_HI),
        inner_lo=op16(constants.OP_INNER_LO),
        inner_hi=op16(constants.OP_INNER_HI),
        arp=op16(constants.OP_ARP),
    )


def iter_frames(song: Song, max_frames: Optional[int] = None):
    """Yield per-frame register write lists for ``song``.

    Stops after ``max_frames`` frames (required for a non-looping render,
    since the Music Assembler player loops forever).
    """
    player = Player(song)
    frame = 0
    while max_frames is None or frame < max_frames:
        yield player.play_frame()
        frame += 1


def render_grid(song: Song, nframes: int) -> List[List[int]]:
    """Render ``nframes`` of forward-filled per-frame register snapshots."""
    player = Player(song)
    rows: List[List[int]] = []
    for _ in range(nframes):
        player.play_frame()
        rows.append(player.regs[:])
    return rows
