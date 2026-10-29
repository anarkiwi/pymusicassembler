"""Writer: round-trip stability and re-emission."""

import pytest

from pymusicassembler import constants, reader, writer
from pymusicassembler.errors import SongValidationError
from pymusicassembler.model import Arpeggio, ArpeggioRow, OrderEntry, Orderlist, Song


def test_build_is_image_stable(tune_path):
    """build(read(image)) reproduces the source image byte-for-byte."""
    song = reader.read(tune_path)
    assert writer.build(song) == song.image


def test_read_write_song_is_idempotent(tune_path):
    """read(write(song)) == song structurally."""
    song = reader.read(tune_path)
    rebuilt = reader.parse(reader.load_prefix(song.load_addr) + writer.build(song))
    assert rebuilt.orderlists == song.orderlists
    assert rebuilt.patterns == song.patterns
    assert rebuilt.instruments == song.instruments
    assert rebuilt.freq_lo == song.freq_lo
    assert rebuilt.freq_hi == song.freq_hi
    assert rebuilt.inner_programs == song.inner_programs


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
    assert blob[:2] == reader.load_prefix(song.load_addr)
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
    """Editing a pattern changes the emitted image at that address."""
    song = reader.read(tune_path)
    before = writer.build(song)
    song.patterns[0].data[:] = [0x10, 0x05]
    after = writer.build(song)
    assert before != after


@pytest.mark.parametrize(
    "data",
    [
        [0x10],  # a note whose control byte would be the terminator
        [0x10, 0x05, 0xFF, 0x10, 0x05],  # a row-start $FF ends the pattern early
    ],
)
def test_build_rejects_a_pattern_the_player_misreads(tune_path, data):
    song = reader.read(tune_path)
    song.patterns[0].data[:] = data
    with pytest.raises(SongValidationError, match="pattern 0"):
        writer.build(song)


def test_build_rejects_an_orderlist_the_player_misreads(tune_path):
    song = reader.read(tune_path)
    song.orderlists[0].entries.insert(0, OrderEntry(constants.SILENCE_PATTERN))
    with pytest.raises(SongValidationError, match="voice 0 orderlist"):
        writer.build(song)


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
    assert orderlist.to_bytes() == expected


def test_silence_terminator_round_trips_byte_exact(silence_tune_path):
    """Voices parked on the single-byte $FE: build must not pad a $FF past the end."""
    song = reader.read(silence_tune_path)
    assert any(ol.terminator == constants.SILENCE_PATTERN for ol in song.orderlists)
    built = writer.build(song)
    assert built == song.image
    prg = reader.load_prefix(song.load_addr) + built
    assert reader.parse(prg).orderlists == song.orderlists


def test_jump_terminator_round_trips_byte_exact(jump_tune_path):
    """Voices ending on a $FD n jump: build emits the jump and nothing past it."""
    song = reader.read(jump_tune_path)
    assert any(ol.terminator == constants.ORDER_JUMP for ol in song.orderlists)
    built = writer.build(song)
    assert built == song.image
    prg = reader.load_prefix(song.load_addr) + built
    assert reader.parse(prg).orderlists == song.orderlists


def test_arpeggio_edit_within_reserved_region(tune_path):
    """Fitting programs re-emit at their pointers; a later one shifts past a growth."""
    song = reader.read(tune_path)
    stored = [i for i, prog in enumerate(song.inner_programs) if prog is not None]
    first, second = stored[0], stored[1]
    song.inner_programs[first].rows += [ArpeggioRow(0x21, 3, 0)]
    song.inner_programs[second].rows = song.inner_programs[second].rows[:1]
    built = writer.build(song)
    reparsed = reader.parse(reader.load_prefix(song.load_addr) + built)
    assert reparsed.inner_programs == song.inner_programs
    assert reparsed.orderlists == song.orderlists
    assert reparsed.patterns == song.patterns


def test_arpeggio_growth_past_reserved_region_rejected(tune_path):
    """Growth that overflows the file's reserved region raises, not overwrites tables."""
    song = reader.read(tune_path)
    prog = next(p for p in song.inner_programs if p is not None)
    prog.rows += [ArpeggioRow(0x21, 0, 0)] * 40
    with pytest.raises(SongValidationError, match="reserves"):
        writer.build(song)


def test_added_arpeggio_goes_past_image_end(tune_path):
    """A program added to a placeholder slot is stored past the image end."""
    song = reader.read(tune_path)
    slot = next(i for i, p in enumerate(song.inner_programs) if p is None)
    song.inner_programs[slot] = Arpeggio(rows=[ArpeggioRow(0x41, 0, 0)])
    built = writer.build(song)
    assert len(built) == len(song.image) + 4
    reparsed = reader.parse(reader.load_prefix(song.load_addr) + built)
    assert reparsed.inner_programs == song.inner_programs
    assert reparsed.instruments == song.instruments


def test_inner_programs_restore_byte_exact(tune_path):
    """Editing and restoring a program re-emits the source image byte-for-byte."""
    song = reader.read(tune_path)
    original = bytes(song.image)
    slot = next(i for i, p in enumerate(song.inner_programs) if p is not None)
    song.inner_programs[slot].rows.append(ArpeggioRow(0x21, 1, 0))
    song.inner_programs[slot].rows.pop()
    assert writer.build(song) == original


def test_validate_rejects_no_image():
    with pytest.raises(SongValidationError):
        writer.build(Song())


def test_validate_rejects_bad_orderlist_count():
    song = Song(image=b"\x00" * 16)
    song.orderlists = song.orderlists[:2]
    with pytest.raises(SongValidationError):
        writer.validate(song)


def test_validate_rejects_bad_inner_programs_count():
    song = Song(image=b"\x00" * 16)
    song.inner_programs = song.inner_programs[:8]
    with pytest.raises(SongValidationError, match="inner programs"):
        writer.validate(song)


def test_validate_rejects_out_of_range_transpose():
    with pytest.raises(Exception):
        OrderEntry(pattern_id=0, transpose=99).control_byte()


def test_validate_rejects_io_image(tune_path):
    """A song placed in the C64 I/O area cannot be emitted (it would read live I/O)."""
    song = reader.read(tune_path)
    song.load_addr = 0xD000
    with pytest.raises(SongValidationError, match=r"I/O area"):
        writer.validate(song)


def test_build_rejects_io_image(tune_path):
    """build() refuses a song whose image reaches the I/O area (the editor's $C000 case)."""
    song = reader.read(tune_path)
    song.load_addr = 0xC000
    song.image = bytes(0x1868)  # $C000 + $1868 = $D868 reaches the C64 I/O area
    with pytest.raises(SongValidationError, match=r"I/O area"):
        writer.build(song)


def test_write_rejects_unknown_container(tune_path, tmp_path):
    song = reader.read(tune_path)
    with pytest.raises(SongValidationError):
        writer.write(song, tmp_path / "x", container="nope")


def test_build_rejects_song_located_only_after_init():
    song = Song(layout_post_init=True)
    with pytest.raises(SongValidationError, match="read-only"):
        writer.build(song)


def test_unreferenced_patterns_outside_the_file_rebuild(tune):
    """Ids no orderlist plays whose pointer leaves the file are None and kept."""
    song = reader.read(tune("MUSICIANS/F/Feekzoid/Journey-Second_Movement.sid"))
    assert song.patterns[7] is None and song.patterns[10] is None
    assert writer.build(song) == song.image


def test_build_without_program_jump_rebuilds(tune):
    """A build whose programs have no $FD jump reads $FD as wave data."""
    song = reader.read(tune("MUSICIANS/F/Feekzoid/Journey.sid"))
    assert song.layout.program_jump == 0
    assert writer.build(song) == song.image
