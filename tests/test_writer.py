"""Writer: round-trip stability and re-emission."""

import pytest

from pymusicassembler import constants, reader, writer
from pymusicassembler.errors import SongValidationError
from pymusicassembler.model import OrderEntry, Orderlist, Song


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


@pytest.mark.parametrize(
    "orderlist,expected",
    [
        # $FF terminator: two entries (two bytes each) plus the loop marker.
        (
            Orderlist(entries=[OrderEntry(0x01, 0, 0), OrderEntry(0x02, 1, 5)]),
            bytes([0x01, 0x00, 0x02, 0x15, 0xFF]),
        ),
        # single-byte $FE silence: the last entry emits only its $FE byte, no $FF.
        (
            Orderlist(
                entries=[OrderEntry(0x01, 0, 0), OrderEntry(0xFE)],
                terminator=constants.SILENCE_PATTERN,
            ),
            bytes([0x01, 0x00, 0xFE]),
        ),
        # $FD n jump: the last entry emits its two bytes ($FD n), no $FF.
        (
            Orderlist(
                entries=[OrderEntry(0x01, 0, 0), OrderEntry(0xFD, 0, 3)],
                terminator=constants.ORDER_JUMP,
            ),
            bytes([0x01, 0x00, 0xFD, 0x03]),
        ),
        # an empty $FF-terminated list emits just the loop marker.
        (Orderlist(entries=[]), bytes([0xFF])),
    ],
)
def test_orderlist_bytes_emits_exactly_its_terminator(orderlist, expected):
    assert (
        writer._orderlist_bytes(orderlist) == expected
    )  # pylint: disable=protected-access


def test_silence_terminator_round_trips_byte_exact(silence_tune_path):
    """Voices parked on the single-byte $FE: build must not pad a $FF past the end."""
    song = reader.read(silence_tune_path)
    assert any(ol.terminator == constants.SILENCE_PATTERN for ol in song.orderlists)
    built = writer.build(song)
    assert built == song.image
    prg = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + built
    assert reader.parse(prg).orderlists == song.orderlists


def test_jump_terminator_round_trips_byte_exact(jump_tune_path):
    """Voices ending on a $FD n jump: build emits the jump and nothing past it."""
    song = reader.read(jump_tune_path)
    assert any(ol.terminator == constants.ORDER_JUMP for ol in song.orderlists)
    built = writer.build(song)
    assert built == song.image
    prg = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + built
    assert reader.parse(prg).orderlists == song.orderlists


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
