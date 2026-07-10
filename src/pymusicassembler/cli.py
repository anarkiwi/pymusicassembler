"""Command line interface: song info, register logs, and WAV rendering."""

import argparse
import sys

from pymusicassembler import audio, reglog, writer
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.reader import read


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


def _reglog(args) -> None:
    song = read(args.song)
    frames = round(args.seconds * 50)
    writes = reglog.iter_register_writes(song, max_frames=frames)
    reglog.write_reglog(writes, args.output)
    print(f"wrote {args.output}")


def _wav(args) -> None:
    song = read(args.song)
    audio.render_wav(song, args.output, seconds=args.seconds, model=args.model)
    print(f"wrote {args.output}")


def _resave(args) -> None:
    song = read(args.song)
    writer.write(song, args.output, container=args.container)
    print(f"wrote {args.output}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pymusicassembler", description="Music Assembler song tools"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="print song metadata")
    info.add_argument("song", help="Music Assembler .sid/.prg file")
    info.set_defaults(func=_info)

    log = commands.add_parser("reglog", help="write a SID register log")
    log.add_argument("song", help="Music Assembler .sid/.prg file")
    log.add_argument("output", help="register log file to write")
    log.add_argument("--seconds", type=float, default=60.0)
    log.set_defaults(func=_reglog)

    wav = commands.add_parser("wav", help="render through an emulated SID")
    wav.add_argument("song", help="Music Assembler .sid/.prg file")
    wav.add_argument("output", help="WAV file to write")
    wav.add_argument("--seconds", type=float, default=60.0)
    wav.add_argument("--model", choices=audio.CHIP_MODELS, default="8580")
    wav.set_defaults(func=_wav)

    resave = commands.add_parser(
        "resave", help="re-emit the tune image (--container native = editor S. song)"
    )
    resave.add_argument("song", help="Music Assembler .sid/.prg file")
    resave.add_argument("output", help="output file")
    resave.add_argument(
        "--container", choices=("auto", "psid", "prg", "native"), default="auto"
    )
    resave.set_defaults(func=_resave)
    return parser


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (MusicAssemblerError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
