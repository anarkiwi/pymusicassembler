"""Constants for the Music Assembler player and song format.

Values follow the decompiled Music Assembler player (Richard Bayliss /
Eric Campbell era), cross-checked byte-exact against the ``sidtrace``
register oracle.  The player code is identical across Music Assembler
tunes; only its DATA (orderlists, patterns, instruments and the addresses
they live at) differs per tune.
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

# Player constants recovered from the disassembly.
TEMPO_DIVIDER = 2  # play-call divider ($1090 reset value)
FILTER_STEP = 0xF0  # cutoff sweep increment per owner frame ($129e += step)
FILTER_CTR = 0x29  # cutoff sweep gate-counter reset ($1296)
RES_PRESETS = (0xF1, 0xF7, 0xF7)  # per-voice RES_FILT preset ($12b3)

# Player-code operand offsets-from-load.  The player binary is identical
# across Music Assembler tunes, so each pointer-table base address lives
# at a fixed offset in the code; the reader reads the 16-bit operand
# there to discover the per-tune table base (relocation-safe).
OP_PATTERN_LO = 0x081  # pattern pointer table, low bytes
OP_PATTERN_HI = 0x086  # pattern pointer table, high bytes
OP_ORDER_LO = 0x173  # orderlist pointer table, low bytes
OP_ORDER_HI = 0x178  # orderlist pointer table, high bytes
OP_INSTR = 0x215  # instrument record table base
OP_FREQ_LO = 0x0EC  # frequency table, low bytes
OP_FREQ_HI = 0x0F5  # frequency table, high bytes
OP_INNER_LO = 0x3C6  # instrument inner (effect program) pointer table, low
OP_INNER_HI = 0x3CB  # instrument inner pointer table, high
OP_ARP = 0x2C2  # 4-entry arp direction table

# Fixed table sizes the reader extracts.
FREQ_TABLE_LEN = 96  # note frequency table entries

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

# Per-voice work-RAM absolute addresses (voice index added).  The init
# routine ($1048) zeroes only $1081..$1090 (pat_cursor / ctrl /
# order_cursor / dur / pattern_id) and then reloads pattern_id / transrep
# / repeat from each voice's orderlist; EVERY other work-RAM byte keeps
# its loaded-image value.  A tune saved mid-playback bakes live values
# into these addresses, so the player seeds its voice state from the
# image here -- otherwise its first frames diverge from the oracle.
WORK_MASTER_TEMPO = 0x1090  # global play-call tempo counter
WORK_FILTER_OWNER = 0x1262  # filter-sweep owner voice
WORK_NOTE_INDEX = 0x10C9
WORK_FREQ_LO = 0x10CC
WORK_FREQ_HI = 0x10CF
WORK_ARP_SUBSTEP = 0x10DD
WORK_PW_SUBSTEP = 0x10E0
WORK_PW_DIR = 0x10E3
WORK_NOTE_CTL = 0x1141
WORK_INSTR_CURSOR = 0x1144
WORK_VIB_LO_STEP = 0x1147
WORK_VIB_HI_STEP = 0x114A
WORK_PORTA_CTR = 0x114D
WORK_VIB_FREQ_HI = 0x12B6
WORK_VIB_TOGGLE = 0x12B9
WORK_ARP_PHASE = 0x12BD
WORK_INSTR_X8 = 0x13D9
WORK_PW_LO = 0x13DC
WORK_PW_HI = 0x13DF
WORK_VIB_FREQ_LO = 0x13E2
WORK_CTRL_MASK = 0x1031
WORK_FLAGS = 0x00FD  # zero-page; outside the image -> defaults to 0

# Filter-cutoff sweep state / per-tune immediates in the player code region
# ($129e/$1296/$12a0: live accumulator / gate counter / step; $1266/$126b:
# the voice-0 trigger reset immediates the player customises per tune).
WORK_FC_ACC = 0x129E
WORK_FC_CTR = 0x1296
WORK_FC_STEP = 0x12A0
WORK_FC_ACC_RESET = 0x1266
WORK_FC_CTR_RESET = 0x126B

# Player base the absolute work-RAM addresses above are calibrated to.  A
# relocated tune shifts every base-relative work/immediate address (but not
# the zero-page WORK_FLAGS) by (discovered base - WORK_BASE).
WORK_BASE = 0x1000
