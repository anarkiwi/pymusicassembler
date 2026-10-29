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
`auto` re-emits the original container. `read(write(song)) == song` and
`build(read(image)) == image`. Every pattern, orderlist and arpeggio program is
read and checked with the [song grammar](#song-grammar). The reader takes the
instruments from the grammar's `select` rows (a `$80-$9F` note control byte or
inline filter/vibrato argument is not a select). A pattern id no orderlist
plays, whose pointer reaches no pattern, reads as `None` and is not written; a preset table placed last holds
every record up to the image end or the next stored data (the editor saves it
that far, so its last record may be partial), and an editor pattern table
(`pattern_lo` = `pattern_hi` + stored patterns + 1, closed by a zero entry, all
inside the file) adds the patterns no orderlist references. `build` does not
re-emit the zeros the reader fills past the source's end. Each orderlist
re-emits exactly its on-disk terminator — no `$FF` is appended after a `$FE`
silence or a `$FD n` jump. Arpeggio programs re-emit at their slot pointers
without moving — the file's reserved region (through the original pack's end)
bounds any growth, and programs the song adds beyond the file's are appended
past the data end with their slot pointer updated. The writer discovers the
table bases from the same relocation-invariant player-code signatures the reader
uses, so any load address re-emits, not just the standard `$1021` layout.

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
| `P.` preset bank | `$4300`, 768 bytes | 16 arpeggio slots of 32 bytes (`wave, note, filter` rows; `$FE` stop / `$FF` restart; empty slot = all zeroes) + 32 presets × 8 bytes (the `Instrument` record layout) |

`S.` songs read into a `Song` and write back via `container="native"`, which
assembles the editor's layout at any base in `$0400-$FF00` (below).
`P.` banks are
not tunes: `read()` rejects them, and `read_presets()` / `write_presets()`
(#27) handle them as `PresetBank`.

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

HVSC rips of these songs swap the first six bytes for PSID `JMP init` /
`JMP play` vectors (or begin the image at the play routine).
`build_native(song, base=None)` (`write(..., container="native", base=...)`,
`pymusicassembler resave --container native --base $C000`) assembles the editor's
save from the model, relocated to `base` (default: the source's player base,
`tempo_cell - $90`):

| part | contents |
| --- | --- |
| player | the stub for `base`; the source's player through the orderlist pointer table (code, note-frequency tables, arpeggio and orderlist pointers, the work-RAM snapshot), plus player code placed after that table (an order-hook routine) |
| data | patterns, orderlists (voice 3 first), arpeggio programs, `pattern_hi`, `pattern_lo` (each with a closing zero entry), presets |

- Player operands are relocated by walking the player's control flow from its
  play and init entries (`JSR`/`JMP`/branch targets; `CLC; BCC`-style forced
  branches; `RTS`/`RTI`/`BRK`/`JMP ()`/`JAM` end a path); operands into the
  player move with `base`, operands at `pattern_lo`, `pattern_hi` and the
  presets follow their tables, and an operand into other song data raises
  `SongValidationError`.
- The editor saves its data area verbatim, unused bytes included: while items
  keep their source order, the source's bytes between them are kept, and a
  pattern overlapping the previous one (a shared tail) stays inside it. The
  preset table keeps the source's extent (a partial last record stays partial).
- Like the editor's relocation loop, `inner_hi` entries 1-16 (one past the
  table) move by the page delta; empty arpeggio slots keep their placeholder
  pointers, and a slot the song emptied points to `$0000`.
- `SongValidationError` for a player not at the editor's layout (no play entry
  at `base+$21`, tables out of the editor's order), orderlist `$FC`/`$FD` or
  program `$FD` commands, an arpeggio program longer than the editor's 32-byte
  program slot (the editor overwrites the next program with its tail), or a
  base reaching `$D000-$DFFF`.
- A program pitch is masked to 7 bits, so pitches 96-127 read past the 96-entry
  frequency tables into the arpeggio pointer table and player code. Moving the
  programs (reflowing a source not in the editor's order) or relocating
  changes those bytes, as the editor's own load and save does.

Editor files re-emit byte-identically (the release disks' `S.` files and the
editors' re-saves, relocated or not); the stub is the editor's, so a file with
a hand-edited stub differs there. Assembling at another base reproduces the
editor's relocated save but for the work-RAM snapshot. `tests/test_editor_files.py`
checks this on release-disk `S.` files pinned in `tests/fixtures/ma_native.txt`,
each against its HVSC rip.

### `P.` preset bank (`read_presets` / `write_presets`)

The bank is the editor's second save type ("P-SAVE PRESET") — its
"play back your music using different preset banks" feature: 16 arpeggio slots
of 32 bytes (three-byte `wave, note, filter` rows terminated at a row start by
`$FE` stop, `$FF` restart, or a later build's `$FD r` jump; an unused slot is
32 zero bytes), then 32 preset records of 8 bytes in the song's `Instrument`
layout — a single bank can hold presets for several songs on a disk.

```python
bank = ma.read_presets("P.TUNE.p")          # path, bytes, or binary file object
ma.write_presets(bank, "P.OUT.p")           # the editor's 770-byte file: $4300 + 768-byte body
bank = ma.PresetBank()                      # 16 arpeggio slots + 32 presets, edited like a Song
song.apply_preset_bank(bank)                # the song now references the bank's presets + arpeggios
ma.write(song, "S.TUNE.prg", container="native")
```

Every editor version writes the same bytes (V1.0-Triad V1.4 and the
Plus/TKC/Faces cracks, verified against `P.` files saved on the release disks).
`read_presets` accepts the 768-byte body with or without the two-byte load
prefix; `write_presets` emits the editor's exact form, so
`write_presets(read_presets(f)) == f` on release files. `apply_preset_bank`
swaps the song's instruments and arpeggio programs for the bank's, so the
same orderlists and patterns play with the bank's presets.

## Song grammar

The song data the play routine walks is described by one Lark grammar,
[`musicassembler.lark`](../src/pymusicassembler/musicassembler.lark). Each
stream is the run of bytes one of the player's 8-bit cursors reads from a
pointer-table entry, at most `$100` bytes, one grammar character per byte.
Rules are named for what the player does with the bytes. Start rules select
the build's commands from the discovered layout:

| Stream | Pointer table | Start rule (build) |
| --- | --- | --- |
| pattern | `pattern_lo`/`pattern_hi` | `pattern` (all builds) |
| orderlist | `order_lo`/`order_hi` | `orderlist`; `orderlist_hook` (`$FD n`); `orderlist_inline` (`$FC t`, `$FD n`) |
| arpeggio program | `inner_lo`/`inner_hi` | `program`; `program_jump` (`$FD r`) |

- **Pattern** — `row* $FF`. A row is a `note` (`< $60` pitch, then a control
  byte: low five bits duration, bit 6 tie, bit 5 vibrato with two step bytes,
  bit 7 filter with cutoff and sweep bytes), a `rest` (`$60-$7F`, gate off), a
  `hold` (`$A0-$FE`, the note sounds on) or a `select` (`$80-$9F`, instrument
  `id & $1F`) followed by a note or by a rest byte `$60-$FF`. Only a row-start
  `$FF` ends the pattern. The player reads a row before testing for `$FF`, so
  it plays an empty pattern's lone `$FF` as a hold.
- **Orderlist** — `(pattern_id, transpose << 4 | repeat)` entries ending in
  `$FF` (loop to the build's restart entry) or a single `$FE` (park on the
  silence pattern). The order-hook build ends a list on `$FD n` (jump to entry
  `n`); the later build adds `$FC t` (tempo reload) as an entry.
- **Arpeggio program** — `(waveform, pitch, cutoff)` rows ending at a row-start
  `$FF` (restart) or `$FE` (stop). A `$FD r` (continue at row `r`) ends the
  program only on builds whose player tests for it; elsewhere it is waveform
  data.

`grammar.check(song, layout)`, run by `build` and `build_native`, refuses a
stream whose bytes the player would read back differently (a row-start `$FF`
inside a pattern, a mid-list `$FE` entry). `pymusicassembler grammar tune.sid`
prints every stream's parse tree.

## Data model

Three independent per-voice tracks, each a [grammar](#song-grammar) stream:

- **Orderlists** — one per voice. The reader records which ending it met
  (`$FF`, `$FE` or `$FD n`) and `build` re-emits just that terminator: it never
  pads a `$FF` after `$FE` or `$FD n`, which would overwrite the next voice's
  data packed after it.
- **Patterns** — the player bytecode of the `pattern` rule, stored without its
  `$FF`; `Pattern.events()` is its parsed rows.
- **Instruments** — 8-byte records: attack/decay, sustain/release, waveform,
  pulse-width init + sweep step, arp speed + step, porta/effect flags.
- **Arpeggios (inner programs)** — 16 slots indexed by the instrument flags
  low nibble. The editor stores a tune's used programs
  packed back-to-back after the other tables, points the slot table
  (`inner_lo`/`inner_hi`) at them, and leaves the unused slots with
  placeholder pointers (often shared); `Song.inner_programs` holds a program
  per slot, `None` where the slot is a placeholder.
- **Per-frame DSP** — tempo divider (rows advance every `n+1` play calls, `n`
  the per-tune `LDA #n` the play entry reloads it with), 16-bit pulse-width sweep, vibrato/arp
  frequency accumulators, the gate/retrigger idiom, and a self-modified
  filter-cutoff sweep.

Typed model objects: `Song`, `Orderlist`, `OrderEntry`, `Pattern`, `Instrument`,
`Arpeggio` / `ArpeggioRow`, `PresetBank`, and the decoded pattern events
`NoteEvent` (`duration`, `tied`, `vibrato`, `filter`) / `InstrumentEvent` /
`DurationEvent` (`Pattern.events()`).

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
| Resonance stored to a SID mirror (`$D430`) | Crumb | `$1000` / `$1100` |

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
- [Lark](https://github.com/lark-parser/lark) — the parser the song grammar
  runs on.
- [`sidtrace`](https://github.com/anarkiwi/sidtrace) — the `sidplayfp` register
  oracle the player is validated against.
- [pyresidfp](https://pypi.org/project/pyresidfp/) — reSIDfp SID emulation
  (WAV render).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
