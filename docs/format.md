# Music Assembler format

## Overview

Music Assembler (the Richard Bayliss / Eric Campbell era player) is a C64 SID
music system. `pymusicassembler` parses a tune into a typed song model, runs the
playroutine for byte-exact per-frame SID register output, and writes songs back
to a byte-identical tune image.

## Container and detection notes

Tunes are consumed as `.sid` (PSID/RSID) containers or bare `.prg` images
through the shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker)
base. Player builds differ in load address and layout, so
`pymusicassembler.layout` **discovers** every work cell and table from the
operands of the instructions that use them, matched by opcode skeleton nearest
the play entry. The play entry is the `LDX #0; DEC tempo; BMI` the header's play
routine reaches (through `JMP`/`JSR`; the IRQ vectors for play address 0). A
tune whose tables lie outside the loaded image is read after its init runs in
the emulator.

| Build | Layout | Extra commands |
| --- | --- | --- |
| Original | play entry at `base+$21`, init at `base+$48` | none |
| Order hook | as original; orderlist advance `JSR`s a hook | orderlist `$FD n`: jump to entry `n` |
| Later | init before the play entry, `LDX #n` per voice | orderlist `$FC t`: tempo `t`; `$FD n`; wave-program `$FD r`: jump to row `r` |

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
- **Per-frame DSP** — tempo divider (rows advance every `n+1` play calls, `n`
  the per-tune `LDA #n` the play entry reloads it with), 16-bit pulse-width sweep, vibrato/arp
  frequency accumulators, the gate/retrigger idiom, and a self-modified
  filter-cutoff sweep.

Typed model objects: `Song`, `Orderlist`, `OrderEntry`, `Pattern`, `Instrument`,
and the decoded pattern events `NoteEvent` / `InstrumentEvent` / `DurationEvent`
(`Pattern.events()`).

### Notes vs the decompiled reference (decompile.c)

The player was recovered from its disassembly and validated byte-exact against
the `sidtrace` oracle. Facts a naive read of the Ghidra decompile
(`decompile.c`) gets wrong, recovered from the raw disassembly:

- **The bare-duration token (`$60–$7F`) DOES advance the pattern cursor** (the
  `$10c3` `JMP $1187`); the decompile's inlining made it look like an early
  return.
- **Init does not clear most work RAM.** It zeroes `$1081–$1090` and the
  filter-owner immediate, and reloads pattern id / transpose / repeat; every
  other work byte keeps its loaded-image value. The player therefore runs the
  tune's own init in the emulator and keeps all of its state in that memory.
- **The filter owner is also the trigger-reset voice.** A filter command stores
  the voice in the `CPX #n` the trigger frame tests, and its two argument bytes
  in the `LDA #` immediates that trigger reloads the cutoff accumulator and
  sweep counter from; the cutoff sweep runs only on that voice.
- **An instrument byte followed by a duration gates off**, exactly like a bare
  duration (`BCS` to the same code).
- **RES_FILT presets** come from the player's own 3-byte table.

## Player and playback notes

`Player(song)` exposes `.play_frame() -> list[(reg, val)]` and `.regs`;
`iter_frames(song, max_frames)` yields per-frame writes; `render_grid(song,
nframes)` returns the forward-filled 25-register grid. `iter_register_writes`
follows the shared `py*` register-log surface.

### Byte-exact verdict

Validated frame-for-frame against the
[`sidtrace`](https://github.com/anarkiwi/sidtrace) oracle — a patched
`sidplayfp` run in Docker — via the shared `pysidtracker.make_oracle_fixtures`
(PAL, forward-filled 25-register grid, one leading silent play-call aligned
over). `tests/test_oracle_hvsc.py` covers one representative HVSC tune per
supported player variation, each **byte-exact (residual 0)**:

| Variation | Tune | Load / base |
| --- | --- | --- |
| Standard `$1021` PSID (`load = base+$21`) | Space Invadarz on Vacation, 7 Years, 3LUX Intro | `$1021` / `$1000` |
| Image loads at the player base (`load == base`) | Acid Mix | `$1000` / `$1000` |
| Relocated player (`base != $1000`) | Corvus, Dr Doom, Kasza-Nota | `$2e00` / `$1200` / `$1500` |
| Native self-start song wrapped in PSID (`$78` stub) | Eastern Promise | `$1b00` / `$1b00` |

`tests/test_oracle_songs.py` renders one tune per player feature for its whole
HVSC song length:

| Feature | Tune |
| --- | --- |
| Per-tune tempo reload (`LDA #8`) | VHD (Greatrix) |
| Later build (orderlist `$FC`/`$FD`, wave-program `$FD`) | Song (Cooper) |
| Order-hook build (orderlist `$FD`) | Nightmare Slide Preview |
| RES_FILT preset table | Bit Shit |
| Filter owner / trigger-reset values | After8ty 01 |
| Default song 2 (init selects the song) | Holiday Dream 91 Endpart |
| Player copy placed by init | Kids (Hultink) |

All are single-speed. See [oracle-testing.md](oracle-testing.md) for how the
oracle comparison works.

## References

- [Music Assembler](https://www.codebase64.org/) (Richard Bayliss / Eric
  Campbell era player); recovered from its disassembly (`decompile.c`).
- [Music-Assembler V1.4](https://csdb.dk/release/?id=27472) (Triad, 1994) — the
  editor whose native `S.` song format `container="native"` emits.
- [`sidtrace`](https://github.com/anarkiwi/sidtrace) — the `sidplayfp` register
  oracle the player is validated against.
- [pyresidfp](https://pypi.org/project/pyresidfp/) — reSIDfp SID emulation
  (WAV render).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
