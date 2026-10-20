#!/usr/bin/env python3
"""Fetch the Music Assembler editor's P. fixture into the gitignored cache.

Pinned by SHA-256 in ``tests/fixtures/ma_native.txt`` (a hard error on
mismatch); ``--list`` prints cached paths, ``--force`` re-downloads.
"""

import argparse
import hashlib
import io
import os
import re
import zipfile
from pathlib import Path

from pysidtracker import (
    CsdbFetchError,
    fetch_csdb,
    fixture,
    load_manifest,
    read_d64,
    sums_path,
)

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "tests" / "fixtures" / "ma_native.txt"
CACHE = Path(os.environ.get("MA_NATIVECACHE", str(REPO / "tests" / ".native")))
RELEASE_ID = "94388"  # Music Assembler V1.0 release disk (csdb.dk)


def _norm(name: str) -> str:
    """A C64 directory name without its padding: trailing spaces and ``#``."""
    return name.rstrip(" #")


def _safe(name: str) -> str:
    """A cache-safe form of ``name`` (the sums file splits on whitespace)."""
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", name)


def _prgs(data: bytes):
    """``(name, prg)`` pairs in a raw disk image, or in a zip of disk images."""

    def read_disk(blob: bytes):
        try:
            return [(f.name, f.prg) for f in read_d64(blob)]
        except Exception:  # not a readable disk (zip, manual, ...)
            return []

    pairs = read_disk(data)
    if pairs or not data.startswith(b"PK\x03\x04"):
        return pairs
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if not info.is_dir():
                pairs += read_disk(z.read(info))
    return pairs


def fetch(force: bool = False) -> int:
    """Download release ``RELEASE_ID``, pin the manifest's PRGs in the cache."""
    entries = fetch_csdb([RELEASE_ID], CACHE, force=force)
    prgs = {}
    for entry in entries:
        path = CACHE / entry.relpath
        if not path.is_file():
            continue
        for name, prg in _prgs(path.read_bytes()):
            prgs.setdefault(_norm(name), (prg, entry.url))

    lines = []
    for fx in load_manifest(MANIFEST):
        if fx.name not in prgs:
            raise CsdbFetchError(
                f"{fx.name!r}: not found on release {RELEASE_ID} (csdb.dk)"
            )
        data, url = prgs[fx.name]
        sha = hashlib.sha256(data).hexdigest()
        if sha != fx.sha256:
            raise CsdbFetchError(
                f"{fx.name!r}: downloaded SHA-256 {sha[:12]}..."
                f" != pinned {fx.sha256[:12]}..."
            )
        rel = f"prg/{RELEASE_ID}/{_safe(fx.name)}"
        target = CACHE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{sha}  {rel}  {url}")

    sums = sums_path(CACHE)
    known = (
        set(sums.read_text(encoding="utf-8").splitlines()) if sums.exists() else set()
    )
    fresh = [line for line in lines if line not in known]
    if fresh:
        sums.parent.mkdir(parents=True, exist_ok=True)
        with sums.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(fresh) + "\n")
    for line in lines:
        sha, rel, _url = line.split("  ")
        print(f"{sha}  {rel}")
    return len(lines)


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print cached paths")
    args = parser.parse_args(argv)

    if args.list:
        for fx in load_manifest(MANIFEST):
            path = fixture(fx.sha256, CACHE)
            print(f"{fx.name}\t{fx.sha256}\t{path or '<not cached>'}")
        return 0
    return fetch(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
