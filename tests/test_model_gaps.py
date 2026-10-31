"""The song model gives the bytes the spec player reads: instrument records past
the file's end and the rows of a pattern the player reads on through."""

import pytest
from pysidtracker import parse_sid_header, write_psid

from pymusicassembler import constants, reader
from pymusicassembler.model import DurationEvent, NoteEvent
from pymusicassembler.player import Player

from .synthetic import FRAMES, IDS, PATTERNS, note, rest, synthesize
from .test_corpus import SAMPLE
from .test_init_state import _opened, _ptr
from .test_playspec import assert_song_equals_the_6502

MOONLIGHT = "MUSICIANS/D/Defbeat/Moonlight.sid"
LUX = "DEMOS/0-9/3LUX_Intro.sid"
WINDOW = 400
HOLD = DurationEvent(constants.CTL_DUR_MASK, long=True)


def _record(memory, found, iid):
    at = found["instr"] + iid * constants.INSTR_RECORD_SIZE
    return bytes(memory[at : at + constants.INSTR_RECORD_SIZE])


def test_instruments_past_the_files_end_are_the_machines_ram(tune):
    """Moonlight's instruments 28-31 lie past the image; the model holds the RAM
    the player reads there, which the spec player is shown reading unchanged."""
    song = reader.parse(tune(MOONLIGHT).read_bytes())
    found = song.init_state.found
    end = song.load_addr + len(song.image)
    first = found["instr"] + 28 * constants.INSTR_RECORD_SIZE
    assert first < end < first + constants.INSTR_RECORD_SIZE
    player = Player(song)
    player.render_grid(WINDOW)
    for iid in range(28, 32):
        assert song.instruments[iid].to_bytes() == _record(player.memory, found, iid)
    assert any(song.instruments[iid].to_bytes().strip(b"\0") for iid in range(28, 32))
    assert song.instruments[31].to_bytes() == bytes(
        song.init_state.window(found["instr"] + 31 * 8, 8)
    )


@pytest.fixture
def read_on(tune):
    """The synthetic song with an empty pattern leading into rows, as parsed."""
    tail = note(40, 2) + rest(1)
    patterns = {**PATTERNS, "select": [constants.PATTERN_END] + tail}
    built = synthesize(tune(LUX).read_bytes(), patterns)
    packed = write_psid(
        load=built.load_addr,
        init=built.init_addr,
        play=built.play_addr,
        image=built.image,
    )
    return built, reader.parse(packed), [HOLD, NoteEvent(40, 2), DurationEvent(1)]


def test_a_pattern_read_on_past_an_empty_one_is_its_hold_and_rows(read_on):
    built, song, events = read_on
    pid = IDS["select"]
    assert song.patterns[pid].events() == events
    assert song.patterns[pid].to_bytes()[0] == constants.PATTERN_END
    assert_song_equals_the_6502(built, FRAMES)


def test_the_spec_player_plays_the_rows_after_an_empty_pattern(read_on):
    _, song, events = read_on
    player = Player(song, validate=True)
    player.render_grid(FRAMES)
    played = [model for rule, _, model in _opened(player) if rule == "pattern"]
    assert events in played


def _instruments_played(song, subtune):
    player, found = Player(song, subtune, validate=True), song.init_state.found
    played = set()
    for _ in range(WINDOW):
        player.render_grid(1)
        played |= {
            player.memory[found["instr_x8"] + v] >> 3 for v in range(constants.VOICES)
        }
    return player, played


@pytest.mark.parametrize("relpath", SAMPLE)
def test_every_record_and_row_the_spec_player_reads_is_the_models(relpath, tune):
    """Per subtune: each pattern the spec opens parses, at its pointer, to events
    the model holds, and each instrument record a voice selects is the model's
    and the RAM's."""
    data = tune(relpath).read_bytes()
    ran = 0
    for subtune in range(parse_sid_header(data).songs):
        song = reader.parse(data, subtune)
        if not song.play_addr:
            continue
        ran += 1
        state, found = song.init_state, song.init_state.found
        player, played = _instruments_played(song, subtune)
        for iid in played:
            assert song.instruments[iid].to_bytes() == _record(
                player.memory, found, iid
            )
        for rule, base, model in _opened(player):
            if rule == "pattern":
                assert any(
                    _ptr(state, found["pattern_lo"], found["pattern_hi"], pid) == base
                    and pattern.events() == model
                    for pid, pattern in enumerate(song.patterns)
                    if pattern is not None
                )
    if not ran:
        pytest.skip("an interrupt-hosted tune runs its own 6502, not the spec")
