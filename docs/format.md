# Music Assembler format

## Overview

Music Assembler is a C64 SID music system written by MC (Marco Swagerman) and
OPM of Dutch USA-Team (V1.0, February 1989); Triad (Alfatech, King Fisher)
released the bug-fixed V1.1 (1991), V1.2 (1991), V1.3 (1992) and V1.4
(February 1994). `pymusicassembler` parses a tune into a typed song model, runs
the playroutine for byte-exact per-frame SID register output, and writes songs
back to a tune image.

`Multi Voice Assembler` (R. Christer, [CSDb
140932](https://csdb.dk/release/?id=140932), AKA "Music Assembler") is an
unrelated program — do not chase it.

## Container and detection notes

Tunes are consumed as `.sid` (PSID/RSID) containers or bare `.prg` images
through the shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker)
base. Player builds differ in load address and layout, so
`pymusicassembler.layout` **discovers** every work cell and table from the
operands of the instructions that use them, in the routine the play entry's own
control flow reaches (its `JSR`/`JMP` targets and branch offsets, through `JMP`
trampolines), so player copies packed side by side never mix. The play entry is
the `LDX #0; DEC tempo; BMI` the header's play routine reaches, found by running
that routine on the CPU after init (it may self-modify its `JSR`), else by
following its `JMP`/`JSR`s (the IRQ vectors for play address 0). A tune whose
tables lie outside the loaded image is read after its init runs in the emulator.

| Build | Layout | Extra commands |
| --- | --- | --- |
| Original | play entry at `base+$21`, init at `base+$48` | none |
| Order hook | as original; orderlist advance `JSR`s a hook | orderlist `$FD n`: jump to entry `n` |
| Later | init before the play entry, `LDX #n` per voice | orderlist `$FC t`: tempo `t`; `$FD n`; wave-program `$FD r`: jump to row `r` |

`write(song, dst, container="auto"|"psid"|"prg"|"native")` writes a tune image;
`auto` re-emits the original container. `read(write(song)) == song`, and
`build(read(image)) == image` when the source's preset table holds every record
the reader references: the reader takes every `$80-$9F` pattern byte as a
preset reference — note control bytes and inline filter/vibrato arguments
included — and re-emits each referenced record (zero-filled past the source's
end), so a source whose preset table stops earlier than that reference set
re-emits longer (#25). Each orderlist re-emits exactly its on-disk terminator
— no `$FF` is appended after a `$FE` silence or a `$FD n` jump (#24). The
writer discovers the table bases from the same relocation-invariant
player-code signatures the reader uses, so any load address re-emits, not just
the standard `$1021` layout.

## Native editor formats

The editor saves two file types, and every version — V1.0 (Dutch USA-Team,
February 1989, two builds), the Triad bug-fix releases V1.1 (1991), V1.2 (1991), V1.3
(1992), and V1.4 (February 1994) ([CSDb
94388](https://csdb.dk/release/?id=94388), [27470](https://csdb.dk/release/?id=27470),
[92333](https://csdb.dk/release/?id=92333), [27471](https://csdb.dk/release/?id=27471),
[27472](https://csdb.dk/release/?id=27472)), and the Plus (Trash), TKC and Faces
releases — writes the same `S.` layout and the same `P.` bytes (differing only
in the player's saved work-RAM snapshot and, for `S.` files, the chosen load
address):

| file | load | contents |
| --- | --- | --- |
| `S.` song | chosen at save — the editor relocates the song anywhere in `$0400-$FF00` | the player + data, self-starting |
| `P.` preset bank | `$4300`, 768 bytes | 16 arpeggios × 32 rows (`wave, note, filter`; `$FE` stop / `$FF` restart) + 32 presets × 8 bytes (the `Instrument` record layout) |

`S.` songs read into a `Song` and write back via `container="native"`,
byte-identically where the round-trip conditions above hold. `container="native"`
keeps the source's table order and emits at the tune's own base — it does not
yet do the editor's save-time relocation or table reflow, so an export of a
table order the editor cannot load plays but is not an editor song, and builds
the editor cannot represent raise `SongValidationError` (#28). `P.` banks are
not read or written yet — `read()` rejects them (#27).

### `S.` songs (`container="native"`)

A native song is exactly the player+data image this library models
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
address, with no relocation. When the preset table holds the reader's full
reference set the re-emission is byte-for-byte; where the reference set
outruns the stored table, the missing records are zero-filled in and the
re-emission is longer (#25).

### `P.` preset bank (not supported)

The bank is the editor's second save type ("P-SAVE PRESET"): 16 arpeggios of 32
rows (three bytes each: `wave, note, filter`; the arpeggio ends on `$FE` stop
or `$FF` restart), then 32 presets of 8 bytes in the same record layout as the
song's `Instrument` table — the editor's "play back your music using different
preset banks" feature. `read()` currently rejects it (`MusicAssemblerParseError:
no play entry found`); reading and byte-identical writing are tracked in #27.

## Data model

Three independent per-voice tracks:

- **Orderlists** — one per voice, 2-byte entries
  `(pattern_id, transpose << 4 | repeat)`. Each list ends in exactly one of:
  a `$FF` byte (loop to start), a single-byte `$FE` silence entry, or a
  `$FD n` jump entry. The reader records which ending it met and `build`
  re-emits just that terminator — it never pads a `$FF` after `$FE` or `$FD n`
  (which would overwrite the next voice's data packed after it).
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
- **The filter machinery is self-modified code.** Each instruction's own
  operand is the cell it uses, so a copy relocated inconsistently plays as the
  CPU runs it.
- **A silence-pattern (`$FE`) entry parks the voice for good**, so the orderlist
  ends there even without `$FF`.

The player runs on libsidplayfp's memory: RAM holds its power-up pattern and the
PSID driver zeroes `$0000-$03FE`, so records read past the image match. The
tune's play routine runs on the CPU, and every call into a player instance
(there may be several, on several SIDs) is modelled.

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
| Pulse-width high upper nibble | Variation (Ramos) |
| Records past the image read power-up RAM | Triumph (original mix) |
| Orderlist parked on the silence pattern | Evacuation |
| Self-modified play call into a song's copy | Nether (preview) |
| Two player instances, second SID | Double 2SID |

All are single-speed. See [oracle-testing.md](oracle-testing.md) for how the
oracle comparison works.

## References

- [Music-Assembler V1.0](https://csdb.dk/release/?id=94388) (Dutch USA-Team,
  February 1989) — code MC (Marco Swagerman) and OPM; its [user manual](https://csdb.dk/release/download.php?id=170268)
  documents the `S.`/`P.` save formats and the editor's `$0400-$FF00`
  save-time relocation. The player this library models is recovered from its
  disassembly (`decompile.c`).
- [Music-Assembler V1.1](https://csdb.dk/release/?id=27470),
  [V1.2](https://csdb.dk/release/?id=92333),
  [V1.3](https://csdb.dk/release/?id=27471),
  [V1.4](https://csdb.dk/release/?id=27472) (Triad, 1991-1994) — the bug-fix
  releases; all editor versions write the same `S.` layout and `P.` bytes that
  `container="native"` targets.
- [Multi Voice Assembler](https://csdb.dk/release/?id=140932) (R. Christer) —
  an unrelated program also credited as "Music Assembler".
- [`sidtrace`](https://github.com/anarkiwi/sidtrace) — the `sidplayfp` register
  oracle the player is validated against.
- [pyresidfp](https://pypi.org/project/pyresidfp/) — reSIDfp SID emulation
  (WAV render).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
