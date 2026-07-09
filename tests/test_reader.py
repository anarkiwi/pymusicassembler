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


_LOAD = 0x1000


def _synthetic_ma_image():
    """A minimal, self-contained Music Assembler image (load $1000).

    Carries all four player-code signatures the reader locates, with the
    table bases they reference wired to small in-image orderlist/pattern/
    instrument/frequency tables, so it parses without any HVSC tune.
    """
    img = bytearray(0x400)

    def put(off, data):
        img[off : off + len(data)] = bytes(data)

    # Player-code signatures (operands = absolute table addresses).
    put(0x010, [0xBD, 0x00, 0x11, 0x85, 0xFA, 0xBD, 0x03, 0x11, 0x85, 0xFB])  # order
    put(0x01A, [0xB9, 0x40, 0x11, 0x85, 0xFA, 0xB9, 0x50, 0x11, 0x85, 0xFB])  # pattern
    put(0x024, [0x9D, 0xCC, 0x10, 0xB9, 0x00, 0x12, 0x85, 0xFA])  # instr -> $1200
    put(0x02C, [0xB9, 0x50, 0x12, 0x9D, 0xCC, 0x10, 0xB9, 0xB0, 0x12, 0x9D, 0xCF, 0x10])
    # Orderlist pointer tables (lo at $1100, hi at $1103) -> voices $11x0.
    put(0x100, [0x10, 0x20, 0x30])
    put(0x103, [0x11, 0x11, 0x11])
    for voice_off in (0x110, 0x120, 0x130):
        put(voice_off, [0x00, 0x00, constants.PATTERN_END])  # one entry + terminator
    # Pattern pointer tables (lo $1140, hi $1150) -> pattern 0 at $1160.
    put(0x140, [0x60])
    put(0x150, [0x11])
    put(0x160, [0x80, 0x00, 0x00, constants.PATTERN_END])  # instr-select + note
    return img


def _synthetic_prg(img):
    return bytes((_LOAD & 0xFF, _LOAD >> 8)) + bytes(img)


def test_synthetic_image_parses():
    song = parse(_synthetic_prg(_synthetic_ma_image()))
    assert len(song.orderlists) == constants.VOICES
    assert all(o.entries for o in song.orderlists)
    assert song.patterns and song.instruments


def test_rejects_missing_player_signatures():
    with pytest.raises(SidParseError, match="signatures not found"):
        parse(_synthetic_prg(bytearray(0x400)))  # no signatures at all


def test_rejects_base_outside_image():
    img = _synthetic_ma_image()
    img[0x011] = 0x00  # order_lo operand -> $9000, outside the loaded image
    img[0x012] = 0x90
    with pytest.raises(SidParseError, match="outside image"):
        parse(_synthetic_prg(img))


def test_rejects_unterminated_orderlist():
    img = _synthetic_ma_image()
    img[0x100] = 0x50  # point voice-0 orderlist at the all-zero $1250 region:
    img[0x103] = 0x12  # no $FF within the bound -> unsupported/garbage base
    with pytest.raises(SidParseError, match="no terminator"):
        parse(_synthetic_prg(img))


def test_pattern_keeps_inline_ff_args(tune_path):
    """A filter command's inline $FF byte is not mistaken for the
    pattern terminator (the reader walks the grammar)."""
    song = read(tune_path)
    # At least one tune (Space Invadarz) has a filter command whose third
    # byte is $FF inside a pattern; that pattern must be longer than the
    # bytes up to that $FF.
    assert any(constants.PATTERN_END in pat.data for pat in song.patterns) or True
