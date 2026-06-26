"""Typed model: order entries, pattern grammar decoding, instruments."""

import pytest

from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.model import (
    DurationEvent,
    Instrument,
    InstrumentEvent,
    NoteEvent,
    OrderEntry,
    Pattern,
)


def test_order_entry_pack_unpack():
    entry = OrderEntry(pattern_id=0x12, transpose=3, repeat=5)
    assert entry.control_byte() == (3 << 4) | 5
    back = OrderEntry.from_bytes(0x12, (3 << 4) | 5)
    assert back == entry


@pytest.mark.parametrize("transpose,repeat", [(-1, 0), (16, 0), (0, -1), (0, 16)])
def test_order_entry_range_checks(transpose, repeat):
    with pytest.raises(MusicAssemblerError):
        OrderEntry(pattern_id=0, transpose=transpose, repeat=repeat).control_byte()


def test_instrument_round_trip():
    raw = bytes(range(8))
    instr = Instrument.from_bytes(raw)
    assert instr.to_bytes() == raw
    assert instr.attack_decay == 0
    assert instr.porta == 7


def test_instrument_short_bytes_padded():
    instr = Instrument.from_bytes(b"\x01\x02")
    assert instr.to_bytes() == b"\x01\x02\x00\x00\x00\x00\x00\x00"


def test_pattern_terminator():
    pattern = Pattern(data=[0x10, 0x01])
    assert pattern.to_bytes() == bytes([0x10, 0x01, 0xFF])


def test_pattern_events_note():
    # note 0x10, control byte 0x05 (duration 5, no inline args).
    events = Pattern(data=[0x10, 0x05]).events()
    assert events == [NoteEvent(0x10, 0x05, ())]


def test_pattern_events_instrument_then_note():
    # instrument select 0x82 (id 2), then note 0x18 + control 0x01.
    events = Pattern(data=[0x82, 0x18, 0x01]).events()
    assert events[0] == InstrumentEvent(2)
    assert events[1] == NoteEvent(0x18, 0x01, ())


def test_pattern_events_instrument_then_duration():
    # instrument select 0x82, then a bare duration token 0x69 (dur 9).
    events = Pattern(data=[0x82, 0x69]).events()
    assert events == [InstrumentEvent(2), DurationEvent(9)]


def test_pattern_events_long_duration():
    events = Pattern(data=[0xA5]).events()
    assert events == [DurationEvent(5, long=True)]


def test_pattern_events_filter_args():
    # note 0x18, control 0x81 (filter bit), 2 inline arg bytes.
    events = Pattern(data=[0x18, 0x81, 0x39, 0xFF]).events()
    assert events == [NoteEvent(0x18, 0x81, (0x39, 0xFF))]


def test_pattern_events_vibrato_args():
    # note 0x18, control 0x21 (vib/arp bit), 2 inline arg bytes.
    events = Pattern(data=[0x18, 0x21, 0x10, 0x00]).events()
    assert events == [NoteEvent(0x18, 0x21, (0x10, 0x00))]


def test_pattern_events_truncated_note():
    # a trailing lone note byte with no control byte decodes safely.
    assert Pattern(data=[0x10]).events() == [NoteEvent(0x10, 0)]
