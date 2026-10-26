# Usage

Reading a `.sid`/`.prg` into a `Song` and iterating per-frame writes is covered
in the [README](../README.md). This document covers the rest of the API. For the
format and playroutine, see [format.md](format.md).

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
ma.render_wav(song, "tune.wav", seconds=30, progress=True)

# Edit and re-emit, byte-stable.
song.patterns[0].data[0] = ma.constants.DUR_MIN  # tweak a pattern byte
ma.write(song, "tune-edited.sid")

# Export as a native Music Assembler editor song (self-starting ".prg", the
# same layout in every editor version, V1.0-V1.4 and the Plus/TKC/Faces
# releases), at the tune's own base or relocated like the editor's save.
ma.write(song, "S.TUNE.prg", container="native")
ma.write(song, "S.TUNE.prg", container="native", base=0xC000)

# The editor's P. preset bank: read, write, or apply it to a song.
bank = ma.read_presets("P.TUNE.p")
song.apply_preset_bank(bank)               # the song now plays with the bank's presets
ma.write(song, "S.TUNE.prg", container="native")
ma.write_presets(ma.read_presets("P.TUNE.p"), "P.COPY.p")
```

## Command line

```bash
pymusicassembler info   tune.sid
pymusicassembler reglog tune.sid tune.reglog --seconds 30
pymusicassembler wav    tune.sid tune.wav    --seconds 30 --model 8580
pymusicassembler resave tune.sid out.sid     # round-trip write-back (see docs/format.md)
pymusicassembler resave tune.sid S.TUNE.prg --container native  # editor song
pymusicassembler resave tune.sid S.TUNE.prg --container native --base '$C000'  # relocated
```

## Public API

- `read(src, subtune=0) -> Song` / `parse(bytes, subtune=0) -> Song` — read a
  `.sid`/`.prg`. A player found only after init is read from the memory init
  leaves for 0-based `subtune` (a rip whose init selects a different player
  per subtune decodes only the subtunes that select Music Assembler).
- `read_presets(src) -> PresetBank` / `parse_presets(bytes)` — read the
  editor's `P.` preset bank (the 768-byte `$4300` body, with or without its
  load prefix).
- `write(song, dst, container="auto"|"psid"|"prg"|"native", base=None)` — write
  a tune image; `auto` re-emits the original container; `native` emits the Music
  Assembler editor `S.` song (self-starting `.prg`) at `base` (default the
  source's player base).
- `write_presets(bank, dst)` — write the editor's `P.` file (load `$4300` +
  the 768-byte body); `build_presets(bank) -> bytes` — the body;
  `Song.apply_preset_bank(bank)` — make a song reference the bank's presets
  and arpeggios (the editor's "play back with different preset banks").
- `build(song) -> bytes` — the raw C64 image; `build_native(song, base=None) ->
  bytes` — the native self-starting image body in the editor's layout (see
  [format.md](format.md#s-songs-containernative)); `validate(song)`.
- `Player(song, subtune=0)` — `.play_frame() -> list[(reg, val)]`, `.regs`. A `.sid`
  with play address 0 plays every interrupt its init installs (raster, CIA
  IRQs, NMI digis, a main loop) on `pysidtracker.irqhost.InterruptPlayer`,
  one frame per play-source raise as the oracle frames it.
- `formats.MUSIC_ASSEMBLER` — the `musicassembler` `pysidtracker.SidFormat`
  (entry point `pysidtracker.formats`), so `pysidtracker info|reglog|wav` play
  Music Assembler tunes and `format:musicassembler` names this player in
  `pysidtracker.pathdiff`.
- `iter_frames(song, max_frames)` — per-frame writes.
- `render_grid(song, nframes) -> list[list[int]]` — forward-filled grid.
- `render_samples` / `render_wav` / `write_wav` — audio via
  `pysidtracker.audio.render_player_samples` (pyresidfp).
- `iter_register_writes` / `read_reglog` / `write_reglog` / `RegWrite`.
- Model: `Song`, `Orderlist`, `OrderEntry`, `Pattern`, `Instrument`,
  `PresetBank`, and the decoded pattern events `NoteEvent` / `InstrumentEvent`
  / `DurationEvent` (`Pattern.events()`).
- Errors: `MusicAssemblerError`, `MusicAssemblerParseError` (a
  `pysidtracker.SidParseError`; alias `SidParseError`), `SongValidationError`.
