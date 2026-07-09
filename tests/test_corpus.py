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

import os
import sys
from pathlib import Path

import pytest

from pymusicassembler import constants
from pymusicassembler.reader import MusicAssemblerSidParser, read

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

try:  # detect() classification is only asserted when pysidtracker exposes it
    from pysidtracker import PlayroutineKind
except ImportError:  # pragma: no cover - pysidtracker always provides it
    PlayroutineKind = None

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TUNECACHE = Path(
    os.environ.get("MA_TUNECACHE", str(Path(__file__).resolve().parent / ".tunecache"))
)


def _hvsc_root():
    """The local HVSC ``C64Music`` root from ``$HVSC``, or ``None``."""
    root = os.environ.get("HVSC")
    if root and Path(root).is_dir():
        return Path(root)
    return None


def _sample_relpaths():
    """Relative HVSC paths in the committed corpus sample."""
    rels = []
    for line in (FIXTURES / "ma_corpus.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rels.append(line)
    return rels


def _locate(relpath):
    """Absolute path to ``relpath`` under HVSC, the tunecache, or the mirror.

    Prefers a local HVSC tree (``$HVSC``) and the gitignored tunecache; when
    neither has the tune it is fetched from the HVSC mirror into the cache
    (with retries).  Returns ``None`` only when the tune is genuinely
    unreachable after retries, so an individual tune skips cleanly.
    """
    root = _hvsc_root()
    if root is not None:
        cand = root / relpath
        if cand.exists():
            return cand
    cand = TUNECACHE / relpath
    if cand.exists():
        return cand
    try:
        return fetch_tunes.fetch(relpath)
    except Exception:  # pylint: disable=broad-except  # unreachable -> skip
        return None


SAMPLE = _sample_relpaths()


@pytest.fixture(params=SAMPLE, ids=SAMPLE)
def corpus_tune(request):
    """Path to each sampled tune, skipping when it is unavailable locally."""
    path = _locate(request.param)
    if path is None:
        pytest.skip(f"{request.param} unavailable (set $HVSC to the C64Music root)")
    return path


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
    if PlayroutineKind is not None:
        assert detection.kind is PlayroutineKind.DIRECT
