# Music Assembler format

## Overview

Music Assembler (the Richard Bayliss / Eric Campbell era player) is a C64 SID
music system. `pymusicassembler` parses a tune into a typed song model, runs the
playroutine for byte-exact per-frame SID register output, and writes songs back
to a byte-identical tune image.

## Container and detection notes

Tunes are consumed as `.sid` (PSID/RSID) containers or bare `.prg` images
through the shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker)
base. The player binary is identical across tunes; only its DATA and the
addresses that data lives at differ, so the reader **discovers** each table base
from the player code's 16-bit operands (relocation-safe — no fixed-address
assumption). Packed/relocating builds are detected by running the tune's init;
container headers are not trusted.

`write(song, dst, container="auto"|"psid"|"prg"|"native")` writes a tune image;
`auto` re-emits the original container byte-identically. `build(read(image)) ==
image` and the full `.sid` container re-emits byte-identically for every tune;
`read(write(song)) == song`. The writer discovers the table bases from the same
relocation-invariant player-code signatures the reader uses, so any load address
re-emits, not just the standard `$1021` layout.

## Native editor song format (`container="native"`)

The Music Assembler editor (Triad, v1.4, 1994 — [CSDb
release 27472](https://csdb.dk/release/?id=27472)) saves songs as `S.`-prefixed
`.prg` files. A native song is exactly the player+data image this library models
with a **self-starting IRQ-install stub** in place of a PSID header:

```
base+$00  78 20 48 .. A9 18 ...   SEI; JSR init; install IRQ vector -> base+$18;
                                   start CIA/raster IRQ; CLI; RTS   (SYS base plays)
base+$18  EE 19 D0 20 21 .. 4C 31 EA   IRQ: ack raster; JSR play; JMP $EA31
base+$21  <play routine>          (the exact player the reader decodes)
base+$48  <init routine>
...       <order / pattern / instrument / note-freq / wave-program tables>
```

HVSC rips of these songs just swap the first six bytes for PSID `JMP init` /
`JMP play` vectors (or begin the image at the play routine). `container="native"`
reverses that: it re-emits the data, finds the player base from the play-entry
signature (`base = tempo_cell - $90`, where the tempo prescaler is `base+$90`),
and writes the self-start stub back at `base` — at the tune's **own** load
address (no relocation). A song whose image already carries the stub re-emits
byte-for-byte; a source that truncated a referenced instrument record is
re-emitted in full (a few trailing zero bytes longer).

## Data model

Three independent per-voice tracks:

- **Orderlists** — one per voice, 2-byte entries
  `(pattern_id, transpose << 4 | repeat)`, terminated by `$FF` (loop to start);
  pattern id `$FE` is a silence marker.
- **Patterns** — player bytecode: `< $60` note (+ a control byte, with two
  inline argument bytes for a filter (`$80` bit) or vibrato/arp (`$20` bit)
  command); `$60–$7F` bare duration; `$80–$9F` instrument select
  (`id = value & $1F`); `$A0–$FE` long duration; `$FF` terminator.
- **Instruments** — 8-byte records: attack/decay, sustain/release, waveform,
  pulse-width init + sweep step, arp speed + step, porta/effect flags.
- **Per-frame DSP** — tempo divider, 16-bit pulse-width sweep, vibrato/arp
  frequency accumulators, the gate/retrigger idiom, and a self-modified
  filter-cutoff sweep.

Typed model objects: `Song`, `Orderlist`, `OrderEntry`, `Pattern`, `Instrument`,
and the decoded pattern events `NoteEvent` / `InstrumentEvent` / `DurationEvent`
(`Pattern.events()`).

### Notes vs the decompiled reference (decompile.c)

The player was recovered from its disassembly and validated byte-exact against
the `preframr-sidtrace` oracle. Two facts a naive read of the Ghidra decompile
(`decompile.c`) gets wrong, recovered from the raw disassembly:

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

## Player and playback notes

`Player(song)` exposes `.play_frame() -> list[(reg, val)]` and `.regs`;
`iter_frames(song, max_frames)` yields per-frame writes; `render_grid(song,
nframes)` returns the forward-filled 25-register grid. `iter_register_writes`
follows the shared `py*` register-log surface.

### Byte-exact verdict

Validated against the `preframr-sidtrace` register oracle (PAL, 19656
cycles/frame, forward-filled, one leading silent play-call aligned over):

| Tune | Author | Frames | Residual |
| --- | --- | --- | --- |
| Space Invadarz on Vacation | Richard Bayliss | 828 | **0** (byte-exact) |
| 7 Years | Eric Campbell | 828 | **0** (byte-exact) |
| 3LUX Intro | — | 827 | **0** (byte-exact) |

## References

- [Music Assembler](https://www.codebase64.org/) (Richard Bayliss / Eric
  Campbell era player); recovered from its disassembly (`decompile.c`).
- [Music-Assembler V1.4](https://csdb.dk/release/?id=27472) (Triad, 1994) — the
  editor whose native `S.` song format `container="native"` emits.
- `preframr-sidtrace` register oracle.
- [pyresidfp](https://pypi.org/project/pyresidfp/) — reSIDfp SID emulation
  (WAV render).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
