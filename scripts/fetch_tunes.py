#!/usr/bin/env python3
"""Download Music Assembler ``.sid`` test tunes into a gitignored cache.

These tunes are HVSC copyright works and are **never** committed to this
repo (see ``.gitignore``).  They are fetched on demand from a public HVSC
mirror into ``tests/.tunecache/`` (gitignored), so a fresh clone works with
no machine-specific paths.  The byte-exact player tests are skipped when a
tune is absent (and CI has no network), exactly mirroring how the
register-log oracle test is env-gated.

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
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("MA_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.  The relative HVSC
# path is appended verbatim.
MIRROR = os.environ.get("HVSC_MIRROR", "https://hvsc.brona.dk/HVSC/C64Music").rstrip(
    "/"
)

# Transient-failure retry policy for mirror fetches (attempts, fixed backoff).
_FETCH_ATTEMPTS = 4
_FETCH_BACKOFF = 2.0

# id -> HVSC relative path.  Space Invadarz on Vacation is the decompile
# reference (the tune the player byte-exact validation was derived from);
# 7 Years and 3LUX Intro exercise per-tune table relocation and the
# self-modified filter envelope.
TUNES = {
    "space": "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "7years": "DEMOS/0-9/7_Years.sid",
    "3lux": "DEMOS/0-9/3LUX_Intro.sid",
}


def _is_sid(data: bytes) -> bool:
    return data[:4] in (b"PSID", b"RSID")


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path."""
    relpath = relpath.lstrip("/")
    dest = CACHE / relpath
    if dest.exists() and not force:
        return dest
    url = f"{MIRROR}/{relpath}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "pymusicassembler/fetch_tunes"}
    )
    data = None
    last_exc = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(  # nosec B310 (https mirror)
                req, timeout=60
            ) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # genuinely absent -- do not retry
                raise
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < _FETCH_ATTEMPTS - 1:
            time.sleep(_FETCH_BACKOFF)
    if data is None:
        raise RuntimeError(f"{url}: fetch failed after retries: {last_exc}")
    if not _is_sid(data):
        raise RuntimeError(f"{url}: not a SID file (magic {data[:4]!r})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


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
