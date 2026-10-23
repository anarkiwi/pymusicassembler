"""Corpus test: parse a representative sample of real HVSC Music Assembler tunes.

The Music Assembler player is relocated per tune -- across HVSC the same
player loads at $1000, $c000, $9000, ... rather than the single $1021 the
three committed fixtures use.  This test parses a deterministic,
load-stratified sample (``tests/fixtures/ma_corpus.txt`` -- paths only, no
copyright data) drawn from the local HVSC tree, asserting each tune parses
into a complete Song and is statically recognised.

HVSC tunes are copyright works and are never committed.  The sample is read
from ``$HVSC`` (the ``C64Music`` root, e.g.
``/scratch/preframr/hvsc/C64Music``) or, failing that, the gitignored
tunecache; the test SKIPs cleanly when a tune is unavailable and RUNs for
real when the local tree is present.
"""

from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind
from pysidtracker.testing import gather_tune_relpaths

from pymusicassembler import constants
from pymusicassembler.reader import MusicAssemblerSidParser, read

SAMPLE = gather_tune_relpaths(
    [Path(__file__).resolve().parent / "fixtures" / "ma_corpus.txt"]
)


@pytest.fixture(params=SAMPLE, ids=SAMPLE)
def corpus_tune(request, tune):
    """Path to each sampled tune, skipping when it is unavailable."""
    return tune(request.param)


def test_corpus_tune_parses(corpus_tune):
    """Every sampled real HVSC Music Assembler tune parses to a full Song."""
    song = read(corpus_tune)
    assert len(song.orderlists) == constants.VOICES
    assert all(orderlist.entries for orderlist in song.orderlists)
    assert song.patterns  # at least one referenced pattern
    assert song.instruments  # at least one referenced instrument
    assert len(song.freq_lo) == constants.FREQ_TABLE_LEN
    assert len(song.freq_hi) == constants.FREQ_TABLE_LEN


def test_corpus_tune_recognised(corpus_tune):
    """The parser statically recognises each sampled tune (detect == DIRECT)."""
    parser = MusicAssemblerSidParser()
    data = corpus_tune.read_bytes()
    assert parser.parse(data).orderlists
    detection = parser.detect(data, init=False)
    assert detection.recognised
    assert detection.kind is PlayroutineKind.DIRECT
