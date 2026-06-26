# pymusicassembler

A standalone, pure-Python **reader, writer, and player** for
[Music Assembler](https://www.codebase64.org/) C64 SID songs (the Richard
Bayliss / Eric Campbell era player). It parses a Music Assembler tune into a
typed song model, runs the playroutine to produce byte-exact per-frame SID
register output, renders WAV through an emulated SID, and writes songs back to
a byte-identical tune image.

Read/write/play/register-log is **pure stdlib**; only WAV rendering needs the
optional `audio` extra (pyresidfp).

```bash
pip install pymusicassembler          # reader/writer/player/reglog
pip install pymusicassembler[audio]   # + WAV rendering via pyresidfp
```

## Quick start

```python
import pymusicassembler as ma

song = ma.read("tune.sid")            # PSID/.sid or bare .prg

# Per-frame SID register writes (changed registers only, after frame 0).
for writes in ma.iter_frames(song, max_frames=50 * 60):
    ...                               # writes: list[(register, value)]

# Forward-filled 25-register-per-frame snapshot grid (the oracle form).
grid = ma.render_grid(song, nframes=400)

# Register log (clock reg val triples), and WAV via an emulated SID.
ma.write_reglog(ma.iter_register_writes(song, max_frames=2500), "tune.reglog")
ma.render_wav(song, "tune.wav", seconds=30)   # needs the audio extra

# Edit and re-emit, byte-stable.
song.patterns[0].data[0] = ma.constants.DUR_MIN  # tweak a pattern byte
ma.write(song, "tune-edited.sid")
```

CLI:

```bash
pymusicassembler info   tune.sid
pymusicassembler reglog tune.sid tune.reglog --seconds 30
pymusicassembler wav    tune.sid tune.wav    --seconds 30 --model 8580
pymusicassembler resave tune.sid out.sid     # byte-identical round-trip
```

## Public API

- `read(src) -> Song` / `parse(bytes) -> Song` — read a `.sid`/`.prg`.
- `write(song, dst, container="auto"|"psid"|"prg")` — write a tune image;
  `auto` re-emits the original container byte-identically.
- `build(song) -> bytes` — the raw C64 image; `validate(song)`.
- `Player(song)` — `.play_frame() -> list[(reg, val)]`, `.regs`.
- `iter_frames(song, max_frames)` — per-frame writes.
- `render_grid(song, nframes) -> list[list[int]]` — forward-filled grid.
- `render_samples` / `render_wav` / `write_wav` — audio (pyresidfp extra).
- `iter_register_writes` / `read_reglog` / `write_reglog` / `RegWrite`.
- Model: `Song`, `Orderlist`, `OrderEntry`, `Pattern`, `Instrument`, and the
  decoded pattern events `NoteEvent` / `InstrumentEvent` / `DurationEvent`
  (`Pattern.events()`).
- Errors: `MusicAssemblerError`, `SidParseError`, `SongValidationError`.

## The Music Assembler format

Three independent per-voice tracks. The player binary is identical across
tunes; only its DATA and the addresses that data lives at differ, so the
reader **discovers** each table base from the player code's 16-bit operands
(relocation-safe — no fixed-address assumption):

- **Orderlists** — one per voice, 2-byte entries
  `(pattern_id, transpose << 4 | repeat)`, terminated by `$FF` (loop to start);
  pattern id `$FE` is a silence marker.
- **Patterns** — player bytecode: `< $60` note (+ a control byte, with two
  inline argument bytes for a filter (`$80` bit) or vibrato/arp (`$20` bit)
  command); `$60–$7F` bare duration; `$80–$9F` instrument select
  (`id = value & $1F`); `$A0–$FE` long duration; `$FF` terminator.
- **Instruments** — 8-byte records: attack/decay, sustain/release, waveform,
  pulse-width init + sweep step, arp speed + step, porta/effect flags.
- **Per-frame DSP** — tempo divider, 16-bit pulse-width sweep,
  vibrato/arp frequency accumulators, the gate/retrigger idiom, and a
  self-modified filter-cutoff sweep.

### Notes vs the decompiled reference (decompile.c)

The player was recovered from its disassembly and validated byte-exact against
the `preframr-sidtrace` oracle. Two facts a naive read of the Ghidra
decompile (`decompile.c`) gets wrong, recovered from the raw disassembly:

- **The bare-duration token (`$60–$7F`) DOES advance the pattern cursor** (the
  `$10c3` `JMP $1187`); the decompile's inlining made it look like an early
  return.
- **Init does not clear most work RAM.** The init routine zeroes only
  `$1081–$1090` (per-voice cursors / pattern id) and reloads pattern id /
  transpose / repeat; every other per-voice work byte AND the self-modified
  filter envelope (`$129e` accumulator, `$1296` gate, `$12a0` step, the
  `$1265`/`$126a` voice-0 trigger-reset immediates) keep their loaded-image
  values. A tune saved mid-playback bakes live state in, so the player seeds
  from those image bytes — otherwise its first frames diverge.

## Byte-exact verdict

Validated against the `preframr-sidtrace` register oracle (PAL, 19656
cycles/frame, forward-filled, one leading silent play-call aligned over):

| Tune | Author | Frames | Residual |
| --- | --- | --- | --- |
| Space Invadarz on Vacation | Richard Bayliss | 828 | **0** (byte-exact) |
| 7 Years | Eric Campbell | 828 | **0** (byte-exact) |
| 3LUX Intro | — | 827 | **0** (byte-exact) |

Round-trip: `build(read(image)) == image` and the full `.sid` container
re-emits byte-identically for every tune; `read(write(song)) == song`.

## Tests

Test tunes are HVSC copyright works and are **never committed**. They are
fetched on demand (`python scripts/fetch_tunes.py`) into a gitignored cache;
the byte-exact tests validate against the live `preframr-sidtrace` binary when
`$SIDTRACE_BIN` is set, else against the committed frozen oracle grids in
`tests/fixtures/`, so CI passes with neither the binary nor the tunes.

```bash
pip install -e ".[dev]"
./run_tests.sh
SIDTRACE_BIN=/path/to/sidtrace ./run_tests.sh   # validate live, too
```

## License

Apache 2.0 — see `LICENSE`.
