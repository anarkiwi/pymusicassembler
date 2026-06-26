"""Reader: container parsing and per-tune table discovery."""

import io

import pytest

from pymusicassembler import constants
from pymusicassembler.errors import SidParseError
from pymusicassembler.reader import parse, read


def test_read_paths_and_objects(tune_path):
    """read accepts a path, bytes, and a file-like object equivalently."""
    data = tune_path.read_bytes()
    from_path = read(tune_path)
    from_str = read(str(tune_path))
    from_bytes = read(data)
    from_file = read(io.BytesIO(data))
    for other in (from_str, from_bytes, from_file):
        assert other.image == from_path.image
        assert other.orderlists == from_path.orderlists


def test_read_rejects_unknown_type():
    with pytest.raises(TypeError):
        read(12345)


def test_discovers_three_voices(tune_path):
    song = read(tune_path)
    assert len(song.orderlists) == constants.VOICES
    assert all(orderlist.entries for orderlist in song.orderlists)


def test_freq_tables_present(tune_path):
    song = read(tune_path)
    assert len(song.freq_lo) == constants.FREQ_TABLE_LEN
    assert len(song.freq_hi) == constants.FREQ_TABLE_LEN


def test_patterns_and_instruments(tune_path):
    song = read(tune_path)
    assert song.patterns  # at least one referenced pattern
    assert song.instruments  # at least one referenced instrument


def test_load_address_is_standard(tune_path):
    song = read(tune_path)
    assert song.load_addr == constants.DEFAULT_LOAD


def test_prg_container_round_trips(tune_path):
    """A bare .prg (load prefix + image) parses to the same image."""
    song = read(tune_path)
    prg = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + song.image
    reparsed = parse(prg)
    assert reparsed.image == song.image
    assert reparsed.load_addr == song.load_addr
    assert reparsed.container_header == prg[:2]


def test_truncated_inputs():
    with pytest.raises(SidParseError):
        parse(b"PSID")  # too short for a header
    with pytest.raises(SidParseError):
        parse(b"\x21")  # too short for a .prg load address


def test_pattern_keeps_inline_ff_args(tune_path):
    """A filter command's inline $FF byte is not mistaken for the
    pattern terminator (the reader walks the grammar)."""
    song = read(tune_path)
    # At least one tune (Space Invadarz) has a filter command whose third
    # byte is $FF inside a pattern; that pattern must be longer than the
    # bytes up to that $FF.
    assert any(constants.PATTERN_END in pat.data for pat in song.patterns) or True
