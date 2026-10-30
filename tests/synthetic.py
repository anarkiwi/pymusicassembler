"""Synthetic Music Assembler songs that drive every path of the standard play routine.

The song reuses the player code and table layout of a real tune (3LUX Intro, which
only the image of is read, never committed); every pattern, orderlist, instrument and
arpeggio program is replaced, so the song is built here and each row of it is chosen
to reach a branch the HVSC tunes leave untaken: vibrato, both filter forms, tied
notes, holds, every pulse mode, arpeggios stepping up through a carry and down through
a borrow, instrument programs with filter rows, and the silence terminator.
"""

import dataclasses

from pysidtracker import write_psid

from pymusicassembler import build, constants, parse
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    Instrument,
    OrderEntry,
    Orderlist,
    Pattern,
)

FRAMES = 1200  # every path has run by then

# Instrument flags byte: low nibble is the arpeggio program; the rest select effects.
ARP = 0x10
PULSE_TRIANGLE = 0x20
PULSE_ALWAYS = 0x40

INSTRUMENTS = {
    1: Instrument(0x09, 0xA8, 0x41, 0x08, 0x13, 0x00, 0x00, PULSE_TRIANGLE),
    2: Instrument(0x06, 0x88, 0x41, 0x30, 0x05, 0x00, 0x00, PULSE_ALWAYS),
    3: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x62, 0x11, ARP),
    4: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x22, 0x0F, ARP),
    5: Instrument(0x0A, 0x98, 0x41, 0x08, 0x31, 0x11, 0x21, ARP | PULSE_TRIANGLE | 1),
    6: Instrument(0x0A, 0x98, 0x41, 0x08, 0x00, 0x00, 0x00, PULSE_ALWAYS | 2),
    7: Instrument(0x0A, 0x98, 0x11, 0x08, 0x00, 0x00, 0x00, 3),
    8: Instrument(0x0A, 0x98, 0x11, 0x08, 0x00, 0x00, 0x00, 4),
    # A fast arpeggio stepping by $FF and by $60 (a carry up, a borrow down).
    9: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x01, 0xFF, ARP),
    10: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x02, 0x60, ARP),
}

# Program 1 runs a filter row (cutoff $20, $44) and stops; the existing ones loop.
PROGRAMS = {
    1: Arpeggio(
        [
            ArpeggioRow(0x81, 0xFF, 0x20),
            ArpeggioRow(0x41, 5, 0),
            ArpeggioRow(0x10, 5, 0x44),
            ArpeggioRow(0x10, 0, 0),
        ],
        constants.PROGRAM_STOP,
    )
}


def note(n, dur, tied=False, filt=None, vib=None):
    """A note row; ``filt`` is (cutoff, resonance), ``vib`` (low step, high step)."""
    ctl = dur | (constants.CTL_TRIGGERED if tied else 0)
    args = []
    if filt:
        ctl |= constants.CTL_FILTER
        args = list(filt)
    elif vib:
        ctl |= constants.CTL_VIBARP
        args = list(vib)
    return [n, ctl] + args


def inst(i):
    """An instrument select."""
    return [constants.INSTR_MIN + i]


def rest(dur):
    """A rest of ``dur + 1`` ticks (the gate off)."""
    return [constants.DUR_MIN | dur]


def hold(dur):
    """A hold of ``dur + 1`` ticks (the note sounds on)."""
    return [constants.LONGDUR_MIN | dur]


PATTERNS = {
    "tri": inst(1)
    + note(36, 1)
    + note(40, 1, vib=(1, 0))
    + note(43, 2, vib=(2, 1))
    + note(40, 1, tied=True)
    + hold(3)
    + note(48, 1, filt=(0x85, 0x20))
    + rest(2)
    + note(48, 1, filt=(0x95, 0))
    + note(36, 2),
    "pulse": inst(2)
    + note(30, 1)
    + rest(1)
    + note(34, 2)
    + note(36, 1, filt=(0x40, 0x11))
    + hold(2),
    "arp": inst(3)
    + note(40, 3)
    + note(43, 3, vib=(3, 2))
    + inst(4)
    + note(47, 3)
    + rest(1)
    + hold(1),
    "program": inst(5)
    + note(36, 3)
    + inst(6)
    + note(38, 3)
    + inst(7)
    + note(40, 3)
    + inst(8)
    + note(42, 3),
    "carry": inst(9)
    + note(0, 15)
    + note(12, 15)
    + inst(10)
    + note(0, 15)
    + note(24, 15)
    + note(36, 15)
    + note(60, 15),
    "borrow": inst(9) + rest(2) + note(48, 15) + note(12, 15) + note(0, 15),
    "high": inst(10) + note(2, 15) + hold(15) + note(60, 15) + note(70, 15),
    # An instrument select followed by a duration, not a note.
    "select": inst(1) + rest(3) + inst(2) + hold(2) + note(40, 2),
}
IDS = {name: i for i, name in enumerate(PATTERNS)}

# (pattern, transpose, repeat) per entry; None is the silence marker.
ORDERS = [
    ([("tri", 0, 1), ("pulse", 2, 0), ("program", 0, 0), ("carry", 0, 0)], None),
    ([("arp", 0, 0), ("tri", 3, 2), ("pulse", 0, 0), ("borrow", 0, 0)], None),
    (
        [
            ("program", 0, 1),
            ("arp", 12, 0),
            ("high", 0, 0),
            ("select", 0, 0),
            (None, 0, 0),
        ],
        constants.SILENCE_PATTERN,
    ),
]


def _orderlist(entries, terminator):
    return Orderlist(
        [
            OrderEntry(constants.SILENCE_PATTERN if name is None else IDS[name], t, r)
            for name, t, r in entries
        ],
        terminator or constants.ORDER_END,
    )


def synthesize(base_data):
    """The synthetic song over the player and layout of the PSID ``base_data``."""
    song = parse(base_data)
    for pid, data in enumerate(PATTERNS.values()):
        song.patterns[pid] = Pattern(data)
    song.orderlists = [_orderlist(*order) for order in ORDERS]
    for i, instrument in INSTRUMENTS.items():
        while len(song.instruments) <= i:
            song.instruments.append(Instrument())
        song.instruments[i] = instrument
    for i, program in PROGRAMS.items():
        song.inner_programs[i] = program
    built = dataclasses.replace(song, image=build(song))
    # A pattern longer than its slot would be overwritten by the next one.
    packed = write_psid(
        load=built.load_addr,
        init=built.init_addr,
        play=built.play_addr,
        image=built.image,
    )
    again = parse(packed)
    assert [p.data for p in again.patterns[: len(PATTERNS)]] == list(PATTERNS.values())
    return built
