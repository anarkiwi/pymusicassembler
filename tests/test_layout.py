"""Player-cell discovery from the player code (:mod:`pymusicassembler.layout`)."""

import pytest

from pysidtracker import SidImage

from pymusicassembler import Player, layout, reader
from pymusicassembler.errors import MusicAssemblerParseError

SPACE = {
    "entry": 0x1021,
    "tempo": 0x1090,
    "tempo_reload": 0x1035,
    "init": 0x1048,
    "clear": 0x1081,
    "order_lo": 0x14B9,
    "order_hi": 0x14BC,
    "pattern_lo": 0x1532,
    "pattern_hi": 0x152E,
    "ctrl_mask": 0x1031,
    "freq_lo": 0x1437,
    "freq_hi": 0x11C5,
    "freq_lo_w": 0x10CC,
    "freq_hi_w": 0x10CF,
    "note_ctl": 0x1141,
    "filter_owner": 0x1262,
    "fc_acc_reset": 0x1266,
    "fc_ctr_reset": 0x126B,
    "fc_acc": 0x129E,
    "fc_ctr": 0x1296,
    "fc_step": 0x12A0,
    "res_presets": 0x12B3,
    "voice_reg": 0x10C6,
    "arp": 0x12C3,
    "instr": 0x1536,
    "inner_lo": 0x1498,
    "inner_hi": 0x14A8,
    "flags": 0xFD,
    "pitch_skip": 4,
}


def _layout(path):
    image = SidImage.from_bytes(path.read_bytes())
    return Player(reader.parse(path.read_bytes())).layout, image


def test_standard_build_cells(tune_path):
    lay, _ = _layout(tune_path)
    if "Space_Invadarz" in tune_path.name:
        assert {k: getattr(lay, k) for k in SPACE} == SPACE
    assert lay.freq_hi_w == lay.freq_lo_w + 3
    assert lay.tempo_reload == lay.entry + 0x14
    assert not lay.order_tempo and not lay.program_jump


def test_order_jump_hook_build(tune_path):
    lay, _ = _layout(tune_path)
    assert lay.order_jump == ("3LUX" in tune_path.name)


def test_later_build_features(later_tune_path):
    lay, image = _layout(later_tune_path)
    assert lay.order_tempo and lay.order_jump and lay.program_jump
    assert lay.init < lay.entry < lay.tempo
    assert image.peek(lay.tempo_reload - 1) == 0xA9


def _mem(code):
    mem = bytearray(0x10000)
    for addr, data in code.items():
        mem[addr : addr + len(data)] = bytes(data)
    return mem


ENTRY = (0xA2, 0x00, 0xCE, 0x90, 0x10, 0x30, 0x0C)


@pytest.mark.parametrize(
    "play,expected",
    [
        (0x2000, 0x1021),
        (0x2010, 0x1021),
        (0x2020, 0x1021),
        (0, 0x1021),
        (0x3000, 0x3021),
        (0x2030, 0x3021),
    ],
)
def test_resolve_entry_walks_play_routines(play, expected):
    mem = _mem(
        {
            0x1021: ENTRY,
            0x3021: ENTRY,
            0x2000: (0x4C, 0x10, 0x20),
            0x2010: (0xA9, 0x35, 0x85, 0x01, 0x20, 0x21, 0x10, 0x60),
            0x2020: (0x20, 0x00, 0x40, 0x4C, 0x21, 0x10),
            0x2030: (0x4C, 0x30, 0x20),
            0x3000: (0x60,),
            0x0314: (0x10, 0x20),
        }
    )
    assert layout.resolve_entry(mem, play) == expected


def test_resolve_entry_without_player():
    assert layout.resolve_entry(bytes(0x10000), 0x1000) is None


def test_discover_needs_entry_and_reload(tune_path):
    song = reader.read(tune_path)
    view = reader.load_view(song.image, song.load_addr)
    entry = layout.resolve_entry(view, song.play_addr)
    with pytest.raises(MusicAssemblerParseError, match="no play entry"):
        layout.discover(view, entry + 1)
    image = bytearray(song.image)
    image[entry + 0x13 - song.load_addr] = 0xEA
    with pytest.raises(MusicAssemblerParseError, match="no tempo reload"):
        layout.discover(reader.load_view(bytes(image), song.load_addr), entry)


def test_discover_rejects_unknown_pitch_branch(tune_path):
    song = reader.read(tune_path)
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0xC8, 0xB1, 0xFA, 0x30, 0x04, 0x18, 0x7D))) + 4
    image[at] = 0x02
    view = reader.load_view(bytes(image), song.load_addr)
    with pytest.raises(MusicAssemblerParseError, match="pitch_skip"):
        layout.discover(view, layout.resolve_entry(view, song.play_addr))
