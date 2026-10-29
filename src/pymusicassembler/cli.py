"""Command line interface: song info, stream grammars, register logs, WAV, resave."""

import argparse
import sys

from pysidtracker.audio import seconds_to_frames
from pysidtracker.cli import add_reglog_command, add_wav_command, run_cli

from pymusicassembler import audio, constants, grammar, reglog, writer
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.reader import read

_SONG_HELP = "Music Assembler .sid/.prg file"


def _info(args) -> None:
    song = read(args.song)
    print(f"name:        {song.name}")
    print(f"author:      {song.author}")
    print(f"released:    {song.released}")
    print(f"load:        ${song.load_addr:04X}")
    print(f"init/play:   ${song.init_addr:04X} / ${song.play_addr:04X}")
    print(f"patterns:    {len(song.patterns)}")
    print(f"instruments: {len(song.instruments)}")
    for voice, orderlist in enumerate(song.orderlists):
        ids = " ".join(f"{e.pattern_id:02X}" for e in orderlist.entries)
        print(f"  voice {voice}: {len(orderlist.entries)} entries [{ids}]")


def _grammar(args) -> None:
    song = read(args.song, args.subtune)
    for name, stream, rule in grammar.streams(song, song.layout):
        print(f"{name}:")
        for line in grammar.render(grammar.tree(stream.to_bytes(), rule)):
            print(f"  {line}")


def _reglog(args) -> None:
    song = read(args.song)
    frames = seconds_to_frames(
        args.seconds, constants.PAL_CYCLES_PER_FRAME, constants.PAL_CLOCK_HZ
    )
    writes = reglog.iter_register_writes(song, max_frames=frames)
    reglog.write_reglog(writes, args.output)
    print(f"wrote {args.output}")


def _wav(args) -> None:
    song = read(args.song)
    audio.render_wav(song, args.output, seconds=args.seconds, model=args.model)
    print(f"wrote {args.output}")


def _resave(args) -> None:
    song = read(args.song)
    writer.write(song, args.output, container=args.container, base=args.base)
    print(f"wrote {args.output}")


def address(text: str) -> int:
    """A C64 address argument: ``$C000``, ``0xC000`` or decimal."""
    return int(text.replace("$", "0x"), 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pymusicassembler", description="Music Assembler song tools"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="print song metadata")
    info.add_argument("song", help=_SONG_HELP)
    info.set_defaults(func=_info)

    trees = commands.add_parser(
        "grammar", help="print every orderlist, pattern and program parse tree"
    )
    trees.add_argument("song", help=_SONG_HELP)
    trees.add_argument("--subtune", type=int, default=0, help="0-based subtune")
    trees.set_defaults(func=_grammar)

    add_reglog_command(commands, _reglog, song_help=_SONG_HELP)
    add_wav_command(commands, _wav, song_help=_SONG_HELP)

    resave = commands.add_parser(
        "resave", help="re-emit the tune image (--container native = editor S. song)"
    )
    resave.add_argument("song", help=_SONG_HELP)
    resave.add_argument("output", help="output file")
    resave.add_argument(
        "--container", choices=("auto", "psid", "prg", "native"), default="auto"
    )
    resave.add_argument(
        "--base",
        type=address,
        help="native song base address, e.g. $C000 (default: the source's)",
    )
    resave.set_defaults(func=_resave)
    return parser


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    return run_cli(_parser, MusicAssemblerError, argv)


if __name__ == "__main__":
    sys.exit(main())
