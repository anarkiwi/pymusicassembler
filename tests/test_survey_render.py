"""The ``scripts/oracle_hvsc.py`` survey renderer and its render contract."""

import json
import re

import numpy as np
import oracle_hvsc
import pytest

import pysidtracker
from pysidtracker import cadence as cadence_mod
from pysidtracker.cadence import Cadence, playroutine_cadence
from pysidtracker.registers import PAL_CLOCK_HZ, PAL_CYCLES_PER_FRAME
from pysidtracker.sidid import sidid_signatures

from pymusicassembler import MusicAssemblerParseError, Player, reader


def test_render_matches_player(tune_path):
    data = tune_path.read_bytes()
    grid, info = oracle_hvsc.render(data, 1, 120)
    assert grid.shape == (120, 25)
    assert grid.dtype == np.int16
    np.testing.assert_array_equal(grid, Player(reader.parse(data)).render_grid(120))
    assert json.loads(json.dumps(info)) == info
    assert info == {
        "load_offset": 0x21,
        "relocated": False,
        "tempo": 2,
        **Player(reader.parse(data)).layout.as_info(),
        "rsid": False,
        "trigger": "pal_video",
        "speed": 1,
        "dynamic": False,
    }


def test_render_selects_one_based_subtune(tune_path, monkeypatch):
    seen = []

    def player(song, subtune):
        seen.append(("player", subtune))
        return Player(song, subtune=subtune)

    def cadence(data, subtune):
        seen.append(("cadence", subtune))
        return playroutine_cadence(data, subtune=subtune)

    monkeypatch.setattr(oracle_hvsc, "Player", player)
    monkeypatch.setattr(cadence_mod, "playroutine_cadence", cadence)
    oracle_hvsc.render(tune_path.read_bytes(), 3, 10)
    assert sorted(seen) == [("cadence", 2), ("player", 2)]


def test_unknown_cadence_source(tune_path, monkeypatch):
    monkeypatch.setattr(
        cadence_mod,
        "playroutine_cadence",
        lambda data, subtune: Cadence(PAL_CYCLES_PER_FRAME, None, PAL_CLOCK_HZ),
    )
    _, info = oracle_hvsc.render(tune_path.read_bytes(), 1, 10)
    assert info["trigger"] is None
    assert info["speed"] == 1


@pytest.mark.parametrize("data", [b"PSID", b"PSID" + bytes(0x7A) + bytes(0x800)])
def test_non_ma_raises_sid_parse_error(data):
    with pytest.raises(pysidtracker.SidParseError) as exc:
        oracle_hvsc.render(data, 1, 10)
    assert isinstance(exc.value, MusicAssemblerParseError)


def test_player_regex_selects_music_assembler_signatures():
    names = [name for name, _ in sidid_signatures()]
    hits = [n for n in names if re.search(oracle_hvsc.PLAYER, n, re.I)]
    assert hits == ["Music_Assembler", "(Music_Assembler/MC)"]
