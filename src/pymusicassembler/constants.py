"""Constants for the Music Assembler player and song format.

Work-cell and table addresses are per build; :mod:`pymusicassembler.layout`
reads them from the player code.
"""

# Shared C64 hardware register facts (PAL/NTSC timing, SID map) live in
# pysidtracker.registers; re-export them here so the rest of the package keeps
# using ``constants.*`` names.
from pysidtracker import registers as _reg

# SID register map (offsets within the chip's 25 registers).
SID_BASE = _reg.SID_BASE
SID_REG_COUNT = _reg.SID_REG_COUNT
SID_REGISTERS = _reg.SID_REG_COUNT
VOICES = _reg.SID_VOICES
VOICE_REG_SIZE = _reg.SID_VOICE_REG_SIZE
FREQ_LO_REG = _reg.FREQ_LO
FREQ_HI_REG = _reg.FREQ_HI
PW_LO_REG = _reg.PW_LO
PW_HI_REG = _reg.PW_HI
CTRL_REG = _reg.CTRL
AD_REG = _reg.AD
SR_REG = _reg.SR
FC_HI_REG = _reg.FC_HI
RES_FILT_REG = _reg.RES_FILT
MODE_VOL_REG = _reg.MODE_VOL

# Per-voice SID base offsets ($D400 + offset).
SID_VOICE_OFFSET = _reg.SID_VOICE_OFFSET
SID_OFFSET = _reg.SID_VOICE_OFFSET

# Pulse-width high registers carry only their low nibble (12-bit pulse).
PW_HI_REGS = _reg.PW_HI_REGS

# Pattern byte grammar boundaries.
NOTE_MAX = 0x5F  # < 0x60: a note (+ trailing control byte)
DUR_MIN = 0x60  # 0x60..0x7F: bare duration token
INSTR_MIN = 0x80  # 0x80..0x9F: instrument select (id = value & 0x1F)
INSTR_MAX = 0x9F
LONGDUR_MIN = 0xA0  # 0xA0..0xFE: long-duration token (dur = value & 0x1F)
PATTERN_END = 0xFF
ORDER_END = 0xFF  # orderlist terminator -> loop to start
SILENCE_PATTERN = 0xFE  # orderlist pattern id $FE: gate off, no row
ORDER_TEMPO = 0xFC  # orderlist command (later builds): set the tempo reload
ORDER_JUMP = 0xFD  # orderlist command (later builds): jump to entry n
# A real orderlist is at most a few hundred entries (HVSC max 471 entries =
# 942 bytes); a bad table base walks forever, so a terminator must appear
# within this many bytes or the tune is treated as an unsupported variant.
ORDER_MAX_BYTES = 0x1000

# Note-control byte ($1141) bit meanings (the byte after a note).
CTL_FILTER = 0x80  # inline filter command attached
CTL_VIBARP = 0x20  # inline vibrato/arp params attached
CTL_TRIGGERED = 0x40  # note-trigger latch (set after the trigger frame)
CTL_DUR_MASK = 0x1F

# Instrument record byte offsets (8-byte record).
INSTR_RECORD_SIZE = 8
INSTR_AD = 0
INSTR_SR = 1
INSTR_WAVEFORM = 2
INSTR_PULSE_INIT = 3
INSTR_PULSE_STEP = 4
INSTR_ARP_SPEED = 5
INSTR_ARP_STEP = 6
INSTR_PORTA = 7

# Inner (arpeggio) programs, indexed by the instrument flags byte's low nibble.
ARP_COUNT = 16
ARP_ROW_LEN = 3  # one program row: (wave, note, filter)
ARP_MAX_BYTES = 0x100  # a program must terminate within this many bytes

# Program terminator markers (the row-start byte where a new row would begin).
PROGRAM_JUMP = 0xFD  # jump: the following byte is the row to continue at
PROGRAM_STOP = 0xFE  # stop: the program ends after the last row
PROGRAM_RESTART = 0xFF  # restart: the program loops to its first row

# Fixed table sizes the reader extracts.
FREQ_TABLE_LEN = 96  # note frequency table entries

# C64 ROM banks a ROM-free machine reads as empty, not the power-on RAM behind them.
ROM_BANKS = ((0xA000, 0xD000), (0xE000, 0x10000))

# C64 I/O area: $D000-$DFFF holds the CIA/SID/VIC chips while the player runs ($01=$35-$37); a KERNAL LOAD stores into them, not RAM.
IO_AREA = (0xD000, 0xE000)

# C64 timing. A PAL frame is 312 rasterlines x 63 cycles.
PAL_CLOCK_HZ = _reg.PAL_CLOCK_HZ
PAL_CYCLES_PER_FRAME = _reg.PAL_CYCLES_PER_FRAME
NTSC_CLOCK_HZ = _reg.NTSC_CLOCK_HZ
NTSC_CYCLES_PER_FRAME = _reg.NTSC_CYCLES_PER_FRAME

# Standard Music Assembler entry points (PSID header normally matches).
DEFAULT_LOAD = 0x1021
DEFAULT_INIT = 0x1048
DEFAULT_PLAY = 0x1021

# Native Music Assembler editor song ("S." file, Triad v1.4) layout.  The
# native song is the player+data image with a self-starting IRQ-install
# stub at its base in place of a PSID header.  Relative to the player base
# the play routine is at +$21, init at +$48, the IRQ handler at +$18, and
# the tempo-prescaler cell (which anchors the base) at +$90.
NATIVE_PLAY_OFFSET = 0x21
NATIVE_INIT_OFFSET = 0x48
NATIVE_IRQ_OFFSET = 0x18
NATIVE_TEMPO_OFFSET = 0x90
NATIVE_STUB_LEN = 0x21  # bytes the self-start stub occupies (base..base+$20)
NATIVE_IRQ_EXIT = 0xEA31  # KERNAL IRQ return the stub's handler jumps to
