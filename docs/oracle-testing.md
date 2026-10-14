# Oracle testing

`tests/test_oracle_hvsc.py` validates the Music Assembler player byte-for-byte
against the [`sidtrace`](https://github.com/anarkiwi/sidtrace) oracle — a patched
`sidplayfp` run in Docker — reusing the shared
`pysidtracker.make_oracle_fixtures`.

## How it works

1. **Resolve** each tune from a local `$HVSC` tree, else a cache dir, else an
   HVSC mirror download. HVSC `.sid` files are copyright works and are never
   committed.
2. **Render the oracle** with the `anarkiwi/sidtrace` image, which streams a
   zstd CSV of every changed SID write annotated with cycle/interrupt timing.
   The CSV is cached per tune (`.oracle-cache/csv`).
3. **Frame** the CSV into a per-frame 25-register grid; the cadence is the
   median interrupt-raise interval taken from the CSV itself.
4. **Compare** `oracle_hvsc.render(data, 1, ...)` (the survey renderer) against
   the oracle grid, aligning over the player's one leading silent play call (`max_lead`).

The tests are marked `oracle` and excluded from the default suite (they need
Docker + network); a dedicated CI job runs `pytest -m oracle -n auto`. They are
**never skipped** — a broken HVSC download or Docker oracle fails the test
instead of masking a regression.

## Coverage

One representative HVSC tune per supported player variation (standard `$1021`
PSID, image-loads-at-base, relocated player, native self-start song), all
byte-exact over their first 250 frames, plus one tune per player feature over
its whole song (`tests/test_oracle_songs.py`). See the table in [format.md](format.md#byte-exact-verdict).

## HVSC oracle survey

[`scripts/oracle_hvsc.py`](../scripts/oracle_hvsc.py) plugs `Player` into the
[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
whole-song comparison against the `sidtrace` oracle (Docker), over every subtune
of every HVSC tune SIDId names `Music_Assembler` or `(Music_Assembler/MC)`
(`PLAYER`). The `-m oracle` test renders through the same function. Its `info`
reports the decoded build: `load_offset` (image load address minus the nominal
player base, the play entry less `$21`: `0`, or `33` for a rip starting at the
play routine), `relocated` (base other than `$1000`), `tempo` (the play-call
divider reload after init), `features` (the build's `order_tempo`,
`order_jump` and `program_jump` commands), `rsid`, and the play `trigger` and
`speed` (calls per PAL frame, from `pysidtracker.playroutine_cadence`). A tune the
reader cannot decode raises `MusicAssemblerParseError` (a
`pysidtracker.SidParseError`), which the survey records as `not_decompiled`.

```bash
pip install "pysidtracker[survey]>=0.13.0"
python -m pysidtracker.survey find $HVSC --player '^\(?music_assembler(/mc)?\)?$' --out .hvsc-survey/tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC .hvsc-survey/tunes.csv \
    --work .hvsc-survey --render oracle_hvsc:render --jobs 16
python -m pysidtracker.survey report .hvsc-survey/results.csv
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC .hvsc-survey/tunes.csv <md5>_<song> \
    --work .hvsc-survey --render oracle_hvsc:render
```

`.hvsc-survey/` holds copyright-derived traces and is gitignored.

Helpers for working through a survey:

- `scripts/survey_sample.py SRC DST --per-class N` copies a stratified sample
  (whole tunes per failure class, with their cached oracle traces) into `DST`
  for a quick `survey run`; `--tally RESULTS` reports exact/total per class.
- `scripts/layout_census.py HVSC tunes.csv` tallies which player builds
  `pymusicassembler.layout` finds, statically and after init.
- `scripts/disasm.py TUNE.sid [START [END]]` disassembles a tune image.

## Running locally

```bash
pip install -e ".[dev]"
export HVSC=/path/to/HVSC/C64Music          # optional; else fetched from a mirror
docker pull anarkiwi/sidtrace:latest
pytest -m oracle -n auto
```
