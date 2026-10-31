"""The song model holds the orderlists, patterns, programs and instruments a
subtune's init leaves in memory, and ``Song.init_state`` the rest of that memory."""

import pytest
from pysidtracker import load_prefix, parse_sid_header

from pymusicassembler import constants, grammar, reader
from pymusicassembler.player import Player

from .test_corpus import SAMPLE

FRAMES = 400
MEGABLASTIC = "MUSICIANS/B/Beat_Machine/Chris/Megablastic.sid"
SHOOT_EM_UP = "MUSICIANS/F/Feekzoid/Shoot_em_Up.sid"
RULES = {grammar.PATTERN} | {
    grammar.ORDERLIST,
    grammar.ORDERLIST_HOOK,
    grammar.ORDERLIST_INLINE,
}


def _ptr(state, lo, hi, index):
    return state.memory[lo + index] | state.memory[hi + index] << 8


def _opened(player):
    """``(rule, base, model)`` for each stream the spec player opened."""
    for (ref, base), error in player.validator.results.items():
        rule = ref.split(":")[-1]
        if error is None and rule in RULES:
            data = bytes(player.memory[(base + i) & 0xFFFF] for i in range(0x200))
            yield rule, base, grammar.GRAMMAR.parse_prefix(data, rule).model


def _check_streams(data, subtune):
    song = reader.parse(data, subtune)
    player = Player(song, subtune, validate=True)
    player.render_grid(FRAMES)
    state, found = song.init_state, song.init_state.found
    opened = list(_opened(player))
    assert opened
    for rule, base, model in opened:
        if rule == grammar.PATTERN:
            pids = [
                pid
                for pid in range(len(song.patterns))
                if _ptr(state, found["pattern_lo"], found["pattern_hi"], pid) == base
            ]
            assert any(song.patterns[pid].events() == model for pid in pids)
        else:
            voices = [
                v
                for v in range(constants.VOICES)
                if _ptr(state, found["order_lo"], found["order_hi"], v) == base
            ]
            assert any(song.orderlists[v] == model for v in voices)


def _subtunes(relpath, tune):
    data = tune(relpath).read_bytes()
    return data, range(parse_sid_header(data).songs)


@pytest.fixture(params=SAMPLE, ids=SAMPLE)
def sampled(request, tune):
    """The bytes of each sampled tune."""
    return tune(request.param).read_bytes()


def test_every_subtune_streams_are_those_the_spec_opens(sampled):
    """Per subtune, each orderlist and pattern the spec player opens is the model's."""
    songs = parse_sid_header(sampled).songs
    ran = 0
    for subtune in range(songs):
        if reader.parse(sampled, subtune).play_addr:
            _check_streams(sampled, subtune)
            ran += 1
    if not ran:
        pytest.skip("an interrupt-hosted tune runs its own 6502, not the spec")


@pytest.mark.parametrize("relpath", [MEGABLASTIC, SHOOT_EM_UP])
def test_later_subtunes_point_the_voices_elsewhere(relpath, tune):
    """Subtune 1's init repoints the voices; the model follows, the file's lists differ."""
    data, subtunes = _subtunes(relpath, tune)
    first, later = reader.parse(data, 0), reader.parse(data, 1)
    assert 1 in subtunes
    assert later.orderlists != first.orderlists
    state = later.init_state
    for voice, orderlist in enumerate(later.orderlists):
        addr = _ptr(state, state.found["order_lo"], state.found["order_hi"], voice)
        assert orderlist.to_bytes() == bytes(
            state.window(addr, len(orderlist.to_bytes()))
        )
    _check_streams(data, 1)


def test_the_state_is_that_of_the_requested_subtune(tune):
    """The state names its subtune and the voices' cells read from its memory."""
    data = tune(MEGABLASTIC).read_bytes()
    state = reader.parse(data, 2).init_state
    assert state.subtune == 2
    assert "entry=" in repr(state)
    assert state.cell("tempo_reload") == state.memory[state.found["tempo_reload"]]
    assert state.voices("dur") == tuple(
        state.memory[state.found["dur"] + v] for v in range(constants.VOICES)
    )
    assert state.spec is state.spec
    assert state.window(0xFFFF, 2) == bytes((state.memory[0xFFFF], state.memory[0]))


def test_a_bare_prg_boots_its_state_too(tune_path):
    """A .prg has one subtune; its init still leaves a state."""
    song = reader.read(tune_path)
    prg = reader.parse(load_prefix(song.load_addr) + song.image)
    assert prg.init_state.subtune == 0
    assert prg.init_state.entry == prg.layout.entry
