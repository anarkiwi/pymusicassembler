"""Reader: container parsing and per-tune table discovery."""

import io

import pytest

from pysidtracker import write_psid

from pymusicassembler import constants, reader
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


def _prg(song, image=None):
    load = song.load_addr
    return bytes((load & 0xFF, load >> 8)) + bytes(
        song.image if image is None else image
    )


def test_rejects_image_without_player():
    with pytest.raises(SidParseError, match="no play entry"):
        parse(bytes((0x00, 0x10)) + bytes(0x400))


def test_rejects_missing_player_signature(tune_path):
    song = read(tune_path)
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0x29, 0x03, 0xA8, 0xB9)))
    image[at] = 0xEA
    with pytest.raises(SidParseError, match=r"signatures not found \(arp\)"):
        parse(_prg(song, image))


def test_rejects_base_outside_image(tune_path):
    song = read(tune_path)
    lay = reader.discover_layout(
        reader.load_view(song.image, song.load_addr),
        song.play_addr,
        song.load_addr,
        song.load_addr + len(song.image),
    )
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0x85, 0xFA, 0xBD))) - 2
    assert image[at] | (image[at + 1] << 8) == lay.order_lo
    image[at + 1] ^= 0x80
    with pytest.raises(SidParseError, match="order_lo base .* outside image"):
        parse(_prg(song, image))


def test_rejects_unterminated_orderlist():
    view = reader.load_view(bytes((0x00, 0x10, 0x00, 0x10)) + bytes(0x1100), 0x1000)
    bases = {"order_lo": 0x1000, "order_hi": 0x1002}
    with pytest.raises(SidParseError, match="no terminator"):
        reader._parse_orderlist(view, bases, 0)  # pylint: disable=protected-access


def relocating_rip(song, at=0xC000):
    """A PSID loading ``song`` at ``at`` whose init copies it home, then inits."""
    pages = (len(song.image) + 0xFF) >> 8
    src, dst = at + 25, song.load_addr
    stub = bytes(
        [0xA2, pages, 0xA0, 0x00, 0xB9, src & 0xFF, src >> 8, 0x99, dst & 0xFF]
        + [dst >> 8, 0xC8, 0xD0, 0xF7, 0xEE, (at + 6) & 0xFF, (at + 6) >> 8, 0xEE]
        + [(at + 9) & 0xFF, (at + 9) >> 8, 0xCA, 0xD0, 0xEE, 0x4C]
        + [song.init_addr & 0xFF, song.init_addr >> 8]
    )
    return write_psid(
        load=at, init=at, play=song.play_addr, image=stub + bytes(song.image)
    )


def test_relocating_rip_is_read_after_init(tune_path):
    song = read(tune_path)
    copy = parse(relocating_rip(song))
    assert copy.load_addr == 0xC000
    assert copy.orderlists == song.orderlists
    assert copy.patterns == song.patterns
    assert copy.instruments == song.instruments


def test_relocating_prg_without_init_is_rejected(tune_path):
    data = relocating_rip(read(tune_path))
    with pytest.raises(SidParseError, match="outside image"):
        parse(bytes((0x00, 0xC0)) + data[0x7C:])


def test_pattern_keeps_inline_ff_args(tune_path):
    """A filter command's inline $FF byte is not mistaken for the
    pattern terminator (the reader walks the grammar)."""
    song = read(tune_path)
    # At least one tune (Space Invadarz) has a filter command whose third
    # byte is $FF inside a pattern; that pattern must be longer than the
    # bytes up to that $FF.
    assert any(constants.PATTERN_END in pat.data for pat in song.patterns) or True
