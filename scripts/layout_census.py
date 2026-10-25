"""Census of Music Assembler player layouts over a ``survey find`` tune list.

``layout_census.py HVSC TUNES.csv [--jobs N] [--out CSV]`` locates each tune's
play entry and :class:`~pymusicassembler.layout.Layout`, statically and after
emulated init, and tallies the outcomes.
"""

import argparse
import csv
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pysidtracker import SidImage, run_init

from pymusicassembler.errors import MusicAssemblerParseError
from pymusicassembler.layout import discover, resolve_entry


def _locate(image, start, end):
    play = image.header.play_address or image.load
    entry = resolve_entry(image, play, start, end)
    if entry is None:
        return None, "no_entry"
    try:
        lay = discover(image, entry, start, end)
    except MusicAssemblerParseError as exc:
        return entry, str(exc)[:60]
    info = lay.as_info()
    feats = [f for f in ("order_tempo", "order_jump", "program_jump") if info[f]]
    return entry, "+".join(["ok"] + feats)


def census_one(path):
    """``(static, after_init, entry, load)`` outcome labels for one tune."""
    image = SidImage.from_bytes(Path(path).read_bytes())
    entry, static = _locate(image, image.load, image.end)
    if static.startswith("ok"):
        return static, "", entry, image.load
    try:
        run_init(image)
    except Exception as exc:  # pylint: disable=broad-except
        return static, f"init: {exc}"[:60], entry, image.load
    entry, after = _locate(image, 0, 0x10000)
    return static, after, entry, image.load


def main(argv=None):
    """Run the census and print outcome tallies."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("hvsc")
    ap.add_argument("tunes")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    with open(args.tunes, newline="", encoding="utf-8") as f:
        paths = [row["path"] for row in csv.DictReader(f)]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(
            tqdm(
                ex.map(census_one, [Path(args.hvsc) / p for p in paths], chunksize=8),
                total=len(paths),
                unit="tune",
            )
        )
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "static", "after_init", "entry", "load"])
            w.writerows((p,) + r for p, r in zip(paths, rows))
    for (static, after), n in Counter((r[0], r[1]) for r in rows).most_common():
        print(f"{n:6d}  {static:40} {after}")


if __name__ == "__main__":
    main()
