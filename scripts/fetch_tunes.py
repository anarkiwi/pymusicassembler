#!/usr/bin/env python3
"""Download Music Assembler ``.sid`` test tunes into a gitignored cache.

These tunes are HVSC copyright works and are **never** committed to this
repo (see ``.gitignore``).  They are fetched on demand from a public HVSC
mirror into ``tests/.tunecache/`` (gitignored), so a fresh clone works with
no machine-specific paths.  The byte-exact player tests are skipped when a
tune is absent (and CI has no network), exactly mirroring how the
register-log oracle test is env-gated.

The fetch core is :func:`pysidtracker.testing.fetch_tune` (shared across the
``py*`` parsers); this script keeps the Music Assembler corpus list, the cache
location, and the CLI.

Usage::

    python scripts/fetch_tunes.py                # fetch every test tune
    python scripts/fetch_tunes.py --id space     # fetch one
    python scripts/fetch_tunes.py --list         # print id -> HVSC path

Programmatic::

    from scripts.fetch_tunes import fetch, TUNES
    sid_path = fetch(TUNES["space"])
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pysidtracker.testing import DEFAULT_MIRROR, fetch_tune

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("MA_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.  The relative HVSC
# path is appended verbatim.
MIRROR = os.environ.get("HVSC_MIRROR", DEFAULT_MIRROR).rstrip("/")

# id -> HVSC relative path.  Space Invadarz on Vacation is the decompile
# reference (the tune the player byte-exact validation was derived from);
# 7 Years and 3LUX Intro exercise per-tune table relocation and the
# self-modified filter envelope.
TUNES = {
    "space": "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "7years": "DEMOS/0-9/7_Years.sid",
    "3lux": "DEMOS/0-9/3LUX_Intro.sid",
}

# A tune whose image already carries the native self-start stub verbatim
# (``78 20 48 50 ...`` at load $5000): a native editor song wrapped in a
# PSID, used to assert byte-exact native re-emission.  Kept out of TUNES so
# the shared standard-layout tests still parametrize only over $1021 tunes.
NATIVE_TUNE = "DEMOS/A-F/Barbara.sid"


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path."""
    return fetch_tune(relpath, cache_dir=CACHE, mirror=MIRROR, force=force)


def fetch_id(tune_id: str, *, force: bool = False) -> Path:
    """Fetch the tune registered under ``tune_id``."""
    return fetch(TUNES[tune_id], force=force)


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="only this tune id")
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print id -> path")
    args = parser.parse_args(argv)

    if args.list:
        for tid, rel in TUNES.items():
            print(f"{tid}\t{rel}")
        return 0

    ids = [args.id] if args.id else list(TUNES)
    for tid in ids:
        path = fetch_id(tid, force=args.force)
        print(f"{tid}: {TUNES[tid]} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
