"""The ``musicassembler`` :class:`pysidtracker.SidFormat` registration."""

import numpy as np
import oracle_hvsc

from pysidtracker.formats import discover_formats, find_format
from pysidtracker.maincli import main as pysidtracker_main
from pysidtracker.pathdiff import subject

from pymusicassembler import Player
from pymusicassembler.formats import MUSIC_ASSEMBLER, Tune


def test_entry_point_registered():
    assert MUSIC_ASSEMBLER in discover_formats()


def test_auto_recognises_and_plays_like_survey_render(tune_path):
    data = tune_path.read_bytes()
    assert find_format(data, "auto") is MUSIC_ASSEMBLER
    np.testing.assert_array_equal(
        MUSIC_ASSEMBLER.play(data, 0).render_grid(120),
        oracle_hvsc.render(data, 1, 120)[0],
    )


def test_subtune_reaches_player(tune_path):
    data = tune_path.read_bytes()
    tune = MUSIC_ASSEMBLER.load(data, 2)
    assert isinstance(tune, Tune) and tune.subtune == 2
    assert MUSIC_ASSEMBLER.load(data).subtune == 0
    played = MUSIC_ASSEMBLER.play(data, 2)
    assert isinstance(played, Player)
    expected = Player(tune.song, subtune=2).render_grid(60)
    assert played.render_grid(60) == expected


def test_pathdiff_format_side(tune_path):
    data = tune_path.read_bytes()
    side = subject("format:musicassembler", data, 1)
    assert side.label == "musicassembler"
    np.testing.assert_array_equal(
        side.make().render_grid(30), oracle_hvsc.render(data, 1, 30)[0]
    )


def test_info_command(tune_path, capsys):
    assert pysidtracker_main(["info", str(tune_path)]) in (0, None)
    out = capsys.readouterr().out
    song = MUSIC_ASSEMBLER.load(tune_path.read_bytes()).song
    assert "musicassembler" in out
    assert f"patterns: {len(song.patterns)}" in out
    assert song.name in out
