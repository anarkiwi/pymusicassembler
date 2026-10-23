# pymusicassembler

Pure-Python reader, writer, and player for
[Music Assembler](https://csdb.dk/release/?id=94388) C64 SID songs — the Dutch
USA-Team system of MC (Marco Swagerman) and OPM (V1.0, February 1989) and the
Triad bug-fix releases V1.1-V1.4 (1991-1994) — with byte-exact per-frame SID
register output, WAV rendering through an emulated SID, write-back, and the
native editor formats of every editor version: the `S.` song export and the
`P.` preset bank (read, write, and apply to a song).

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: every
work cell and table of the player is located from the player code's own operands
(any load address and player build), and the tune's init runs in a 6502
emulator, so subtune selection and packed/relocating rips play as on a C64.

## Install

```bash
pip install pymusicassembler   # reader/writer/player/reglog and WAV rendering (pyresidfp)
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
validation, and [docs/oracle-testing.md](docs/oracle-testing.md) for the oracle
tests and the whole-HVSC survey.

## Development

```bash
pip install -e ".[dev]"
pysidtracker fixtures fetch tests/fixtures/ma_native.txt  # editor P./S. files into .tunecache/native
./run_tests.sh        # black + pylint + pytest with coverage
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
