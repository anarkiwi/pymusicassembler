#!/usr/bin/env python3
"""Regenerate the deterministic Music Assembler corpus sample.

Enumerates every Music Assembler ``.sid`` under the local HVSC tree
(``$HVSC``, the ``C64Music`` root), keeps those the reader parses
successfully, and writes a deterministic, load-address-stratified sample of
relative paths to ``tests/fixtures/ma_corpus.txt`` (paths only -- the
copyright tunes are never committed).

Candidate tunes are identified by the Music Assembler player signature the
reader itself uses (``MusicAssemblerSidParser.recognize``), so no external
identifier is required; point ``--hvsc`` at the tree and run::

    HVSC=/path/to/C64Music python scripts/sample_corpus.py

The stratification (round-robin across distinct load addresses) maximises
player-relocation/build coverage for a fixed sample size.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path

from pymusicassembler.reader import MusicAssemblerSidParser, parse

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "ma_corpus.txt"
MUST_INCLUDE = (
    "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "DEMOS/0-9/7_Years.sid",
    "DEMOS/0-9/3LUX_Intro.sid",
)
_HEADER = (
    "# Deterministic representative sample of Music Assembler HVSC tunes\n"
    "# (relative to the HVSC C64Music root), used by tests/test_corpus.py.\n"
    "# Paths only -- the copyright tunes are NEVER committed; the test reads\n"
    "# them from $HVSC (or the tunecache) and SKIPs when unavailable.\n"
    "# Stratified across distinct load addresses to exercise every player\n"
    "# relocation/build.  Regenerate via scripts/sample_corpus.py.\n"
)


def _parseable(path: Path):
    """Return the tune's load address if it parses, else ``None``."""
    parser = MusicAssemblerSidParser()
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:4] not in (b"PSID", b"RSID"):
        return None
    image = parser.load_image(data)
    if not parser.recognize(image):
        return None
    try:
        return parse(data).load_addr
    except Exception:  # pylint: disable=broad-except
        return None


def _stratified(by_load: dict, target: int, forced: list) -> list:
    picked, seen = [], set()
    for rel in forced:
        if rel not in seen:
            picked.append(rel)
            seen.add(rel)
    loads = sorted(by_load, key=lambda k: (-len(by_load[k]), k))
    idx = {k: 0 for k in loads}
    while len(picked) < target:
        progressed = False
        for load in loads:
            if len(picked) >= target:
                break
            lst = by_load[load]
            while idx[load] < len(lst) and lst[idx[load]] in seen:
                idx[load] += 1
            if idx[load] < len(lst):
                rel = lst[idx[load]]
                idx[load] += 1
                picked.append(rel)
                seen.add(rel)
                progressed = True
        if not progressed:
            break
    return sorted(picked)


def main(argv=None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hvsc", default=os.environ.get("HVSC"), help="C64Music root")
    ap.add_argument("--count", type=int, default=100, help="sample size")
    args = ap.parse_args(argv)
    if not args.hvsc or not Path(args.hvsc).is_dir():
        ap.error("set $HVSC (or --hvsc) to the local HVSC C64Music root")
    root = Path(args.hvsc)

    by_load = defaultdict(list)
    for path in sorted(root.rglob("*.sid")):
        load = _parseable(path)
        if load is not None:
            by_load[f"{load:#06x}"].append(str(path.relative_to(root)))
    for load in by_load:
        by_load[load].sort()

    sample = _stratified(by_load, args.count, list(MUST_INCLUDE))
    FIXTURE.write_text(_HEADER + "\n".join(sample) + "\n", encoding="utf-8")
    print(
        f"wrote {len(sample)} tunes across {len(by_load)} load addresses -> {FIXTURE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
