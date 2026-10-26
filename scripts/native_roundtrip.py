"""Round-trip editor ``S.`` files (or any tunes) through ``build`` and ``build_native``.

``native_roundtrip.py FILE... [--base ADDR]`` reports, per file, whether the
image and the native export re-emit byte-identically (``--base``: whether the
relocated export re-reads to the same music), and tallies the outcomes.
"""

import argparse
from collections import Counter
from pathlib import Path

from tqdm import tqdm

from pymusicassembler import native, reader, writer
from pymusicassembler.errors import MusicAssemblerError

_MUSIC = (
    "orderlists",
    "patterns",
    "instruments",
    "freq_lo",
    "freq_hi",
    "inner_programs",
)


def _prefix(addr):
    return bytes((addr & 0xFF, addr >> 8))


def roundtrip(path, base=None):
    """``(build, native)`` outcome labels for one file."""
    data = Path(path).read_bytes()
    try:
        song = reader.parse(data)
    except MusicAssemblerError as exc:
        return "unread", type(exc).__name__
    image = _prefix(song.load_addr) + writer.build(song)
    built = (
        "identical" if data in (image, song.container_header + image[2:]) else "differs"
    )
    try:
        at, body = native.assemble(song, base)
    except MusicAssemblerError:
        return built, "rejected"
    if base is not None:
        again = reader.parse(_prefix(at) + body)
        same = all(getattr(again, f) == getattr(song, f) for f in _MUSIC)
        return built, "same_music" if same else "music_differs"
    return built, "identical" if _prefix(at) + body == data else "differs"


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--base", type=lambda t: int(t.replace("$", "0x"), 0))
    args = parser.parse_args(argv)
    tally = Counter()
    for path in tqdm(args.files, unit="file"):
        built, exported = roundtrip(path, args.base)
        tally[(built, exported)] += 1
        if (built, exported) != ("identical", "identical"):
            print(f"{path}\tbuild={built}\tnative={exported}")
    for (built, exported), count in sorted(tally.items()):
        print(f"build={built} native={exported}: {count}")


if __name__ == "__main__":
    main()
