"""Native Music Assembler editor song ("S." file) export."""

import pytest

from pymusicassembler import constants, player, reader, writer
from pymusicassembler.errors import SongValidationError


def _native(song):
    base, body = writer._native(song)  # pylint: disable=protected-access
    return base, body


def test_native_header_is_selfstart(tune_path):
    """The emitted body opens with the self-start stub for its base."""
    song = reader.read(tune_path)
    base, body = _native(song)
    assert body[0] == 0x78  # SEI: the SYS-able self-start entry
    assert body[: constants.NATIVE_STUB_LEN] == writer._native_stub(base)


def test_native_play_routine_follows_stub(tune_path):
    """The MA play routine sits at base+$21, right after the 33-byte stub."""
    song = reader.read(tune_path)
    base, body = _native(song)
    play = body[constants.NATIVE_PLAY_OFFSET : constants.NATIVE_PLAY_OFFSET + 5]
    assert play[:3] == bytes((0xA2, 0x00, 0xCE))  # LDX #0; DEC tempo
    assert play[3] | (play[4] << 8) == base + constants.NATIVE_TEMPO_OFFSET


def test_native_base_matches_form(tune_path):
    """Base is the load address, or load-$21 for a rip that starts at play."""
    song = reader.read(tune_path)
    base, _ = _native(song)
    assert song.load_addr - base in (0, constants.NATIVE_PLAY_OFFSET)


def test_native_reread_idempotent(tune_path, tmp_path):
    """read(write_native(song)) reproduces the song's musical data."""
    song = reader.read(tune_path)
    out = writer.write(song, tmp_path / "song.prg", container="native")
    reparsed = reader.read(out)
    assert reparsed.orderlists == song.orderlists
    assert reparsed.patterns == song.patterns
    assert reparsed.instruments == song.instruments
    assert reparsed.freq_lo == song.freq_lo
    assert reparsed.freq_hi == song.freq_hi


def test_native_selfstart_image_byte_exact(native_tune_path, tmp_path):
    """A tune whose image already carries the stub re-emits byte-for-byte."""
    song = reader.read(native_tune_path)
    assert song.image[0] == 0x78  # sanity: this fixture IS a native self-start
    out = writer.write(song, tmp_path / "song.prg", container="native")
    expected = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + song.image
    assert out.read_bytes() == expected


def test_native_plays_identically(tune_path, tmp_path):
    """The native export plays frame-for-frame identically to the source.

    A form-3 rip (image begins at the play routine) gains the 33-byte stub
    and so loads $0x21 lower; the player must still resolve the tables from
    the shifted operands, so this also guards the player base anchoring.
    """
    song = reader.read(tune_path)
    out = writer.write(song, tmp_path / "song.prg", container="native")
    exported = reader.read(out)
    frames = 400
    assert player.render_grid(exported, frames) == player.render_grid(song, frames)


def test_build_native_matches_write(tune_path, tmp_path):
    """build_native is the body write() prefixes with the base load address."""
    song = reader.read(tune_path)
    base, body = _native(song)
    assert writer.build_native(song) == body
    out = writer.write(song, tmp_path / "song.prg", container="native")
    assert out.read_bytes() == bytes((base & 0xFF, base >> 8)) + body


def test_native_rejects_nonstandard_layout(tune_path, monkeypatch):
    """A layout with no standard player entry cannot be emitted natively."""
    song = reader.read(tune_path)
    monkeypatch.setattr(reader, "find_player_base", lambda *args: None)
    with pytest.raises(SongValidationError):
        writer.build_native(song)
