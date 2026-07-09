# pymusicassembler

Pure-Python reader, writer, and player for
[Music Assembler](https://www.codebase64.org/) C64 SID songs (the Richard
Bayliss / Eric Campbell era player), with byte-exact per-frame SID register
output, WAV rendering through an emulated SID, and byte-identical write-back.

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
player binary is identical across tunes, so each table base is discovered from
the player code's 16-bit operands (relocation-safe) and packed/relocating builds
are detected by running init — container headers are not trusted.

## Install

```bash
pip install pymusicassembler          # reader/writer/player/reglog (pure stdlib)
pip install pymusicassembler[audio]   # + WAV rendering via pyresidfp
```

## Usage

```python
import pymusicassembler as ma

song = ma.read("tune.sid")            # path, bytes, or binary file object; .sid or .prg

# Per-frame SID register writes (changed registers only, after frame 0).
for writes in ma.iter_frames(song, max_frames=50 * 60):
    ...                               # writes: list[(register, value)]
```

See [docs/usage.md](docs/usage.md) for editing/write-back, the register grid,
register logs, WAV rendering, the CLI, and the full public API, and
[docs/format.md](docs/format.md) for the format, playroutine, and byte-exact
validation.

## Development

```bash
pip install -e ".[dev]"
./run_tests.sh        # black + pylint + pytest with coverage
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
