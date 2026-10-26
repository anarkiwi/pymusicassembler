"""Where a tune's player departs from the routine chain :mod:`pymusicassembler.layout` expects.

``layout_mismatch.py TUNE.sid [SONG ...]`` follows the chain from the play entry
the 1-based ``SONG`` reaches after init (default 1) and prints, for the routine
that does not match, each literal byte that differs (``offset expected actual``).
"""

import argparse
from pathlib import Path

from pysidtracker import ChainError, SidImage, follow_jmp, run_init

from pymusicassembler import layout


def mismatch(image: SidImage, entry: int):
    """``(routine, addr, [(offset, expected, actual)])``, or ``None`` when it all matches."""
    try:
        layout.discover(image, entry)
    except layout.MusicAssemblerParseError as exc:
        cause = exc.__cause__
        if not isinstance(cause, ChainError):
            return str(exc), entry, []
        addr = follow_jmp(image, cause.addr)
        routines = layout._CHAIN.routines  # pylint: disable=protected-access
        pattern = dict(routines)[cause.name]
        diffs = [
            (off, val, image.peek(addr + off))
            for off, val in pattern.literals
            if image.peek(addr + off) != val
        ]
        return cause.name, addr, diffs
    return None


def main(argv=None):
    """Print each song's chain mismatch."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tune")
    ap.add_argument("songs", nargs="*", type=int, default=[1])
    args = ap.parse_args(argv)
    data = Path(args.tune).read_bytes()
    for song in args.songs:
        image = SidImage.from_bytes(data)
        run_init(image, song - 1)
        play = image.header.play_address or image.load
        entry = layout.resolve_entry(image, play)
        if entry is None:
            print(f"song {song}: no play entry")
            continue
        found = mismatch(image, entry)
        if found is None:
            print(f"song {song}: entry {entry:#06x} matches")
            continue
        name, addr, diffs = found
        cells = " ".join(f"+{o:02x}:{e:02x}>{a:02x}" for o, e, a in diffs)
        print(f"song {song}: entry {entry:#06x} {name} at {addr:#06x} {cells}")


if __name__ == "__main__":
    main()
