"""Writer: round-trip stability and re-emission."""

import pytest

from pymusicassembler import reader, writer
from pymusicassembler.errors import SongValidationError
from pymusicassembler.model import OrderEntry, Song


def test_build_is_image_stable(tune_path):
    """build(read(image)) reproduces the source image byte-for-byte."""
    song = reader.read(tune_path)
    assert writer.build(song) == song.image


def test_read_write_song_is_idempotent(tune_path):
    """read(write(song)) == song structurally."""
    song = reader.read(tune_path)
    rebuilt = reader.parse(
        bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + writer.build(song)
    )
    assert rebuilt.orderlists == song.orderlists
    assert rebuilt.patterns == song.patterns
    assert rebuilt.instruments == song.instruments
    assert rebuilt.freq_lo == song.freq_lo
    assert rebuilt.freq_hi == song.freq_hi


def test_write_full_file_byte_identical(tune_path, tmp_path):
    """write(song) reproduces the original .sid container byte-for-byte."""
    original = tune_path.read_bytes()
    song = reader.read(original)
    out = writer.write(song, tmp_path / "out.sid")
    assert out.read_bytes() == original


def test_write_prg(tune_path, tmp_path):
    """A .prg re-emission re-reads to the same image."""
    song = reader.read(tune_path)
    out = writer.write(song, tmp_path / "out.prg", container="prg")
    blob = out.read_bytes()
    assert blob[:2] == bytes((song.load_addr & 0xFF, song.load_addr >> 8))
    assert reader.parse(blob).image == song.image


def test_write_synthesized_psid_replays(tune_path, tmp_path):
    """A synthesized PSID (no original header) re-reads to the same song."""
    song = reader.read(tune_path)
    song.container_header = b""  # force synthesis
    out = writer.write(song, tmp_path / "syn.sid", container="psid")
    reparsed = reader.read(out)
    assert reparsed.orderlists == song.orderlists
    assert reparsed.patterns == song.patterns


def test_edit_pattern_changes_image(tune_path):
    """Editing a pattern byte changes the emitted image at that address."""
    song = reader.read(tune_path)
    before = writer.build(song)
    song.patterns[0].data[0] = (song.patterns[0].data[0] + 1) & 0x3F
    after = writer.build(song)
    assert before != after


def test_validate_rejects_no_image():
    with pytest.raises(SongValidationError):
        writer.build(Song())


def test_validate_rejects_bad_orderlist_count():
    song = Song(image=b"\x00" * 16)
    song.orderlists = song.orderlists[:2]
    with pytest.raises(SongValidationError):
        writer.validate(song)


def test_validate_rejects_out_of_range_transpose():
    with pytest.raises(Exception):
        OrderEntry(pattern_id=0, transpose=99).control_byte()


def test_write_rejects_unknown_container(tune_path, tmp_path):
    song = reader.read(tune_path)
    with pytest.raises(SongValidationError):
        writer.write(song, tmp_path / "x", container="nope")
