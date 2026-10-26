"""Real editor-saved ``S.`` songs (cache-only CSDB fixtures, see ``ma_native.txt``)."""

import pytest

from pymusicassembler import native, player, reader, writer

# Editor file -> its HVSC rip (the same song).
EDITOR_SONGS = {
    "s.action_biker": "MUSICIANS/M/MC/Action_Biker.sid",
    "s.dutch_usa_song": "MUSICIANS/M/MC/Untouched.sid",
    "s.game_killer": "MUSICIANS/M/MC/Game_Killer.sid",
    "s.parallax": "MUSICIANS/M/MC/Parallax.sid",
    "s.musix_05": None,
}


def _music(song):
    return (song.orderlists, song.freq_lo, song.freq_hi, song.inner_programs)


@pytest.mark.parametrize("name", EDITOR_SONGS)
@pytest.mark.parametrize("container", ["prg", "native"])
def test_editor_song_rewrites_byte_identical(editor_file, tmp_path, name, container):
    """write(read(f)) reproduces the editor's own file, as a .prg and as an S. song."""
    path = editor_file(name)
    out = writer.write(reader.read(path), tmp_path / "out", container=container)
    assert out.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("name", [n for n, rip in EDITOR_SONGS.items() if rip])
def test_editor_song_matches_hvsc_rip(editor_file, tune, name):
    """The editor file holds the HVSC rip's song and plays it identically."""
    song = reader.read(editor_file(name))
    rip = reader.read(tune(EDITOR_SONGS[name]))
    assert _music(song) == _music(rip)
    used = len(rip.patterns)
    assert song.patterns[:used] == rip.patterns
    assert song.instruments[: len(rip.instruments)] == rip.instruments
    assert player.render_grid(song, 1000) == player.render_grid(rip, 1000)


def test_editor_relocation_matches_the_editors(editor_file):
    """s.dutch_usa_song relocated to $C000 is the editor's own $C000 save
    (s.untouched_____) but for the work-RAM snapshot and its preset tail."""
    saved = reader.read(editor_file("s.untouched_____"))
    base, ours = native.assemble(reader.read(editor_file("s.dutch_usa_song")), 0xC000)
    theirs = saved.image
    view, lay, _ = native._source(saved)  # pylint: disable=protected-access
    code = native.walk_code(view, (lay.entry, lay.init), base + 0x21, lay.order_lo)
    operands = [pc + 1 - base for pc, size in code.items() if size == 3]
    assert [ours[o : o + 2] for o in operands] == [theirs[o : o + 2] for o in operands]
    start, end = lay.inner_lo - base, lay.instr - base
    assert ours[start:end] == theirs[start:end]
    relocated = reader.parse(bytes((0, 0xC0)) + ours)
    assert _music(relocated) == _music(saved)
    assert relocated.patterns == saved.patterns
    assert relocated.instruments[: len(saved.instruments)] == saved.instruments


def test_editor_song_last_preset_record_stays_partial(editor_file):
    """The editor saved s.dutch_usa_song's last preset record part-way; it re-emits so."""
    song = reader.read(editor_file("s.dutch_usa_song"))
    lay = native._source(song)[1]  # pylint: disable=protected-access
    assert (song.load_addr + len(song.image) - lay.instr) % 8
    assert writer.build(song) == song.image
