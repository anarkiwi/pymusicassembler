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
4. **Compare** `Player(read(data)).render_grid(...)` against the oracle grid,
   aligning over the player's one leading silent play call (`max_lead`).

The tests are marked `oracle` and excluded from the default suite (they need
Docker + network); a dedicated CI job runs `pytest -m oracle -n auto`. They are
**never skipped** — a broken HVSC download or Docker oracle fails the test
instead of masking a regression.

## Coverage

One representative HVSC tune per supported player variation (standard `$1021`
PSID, image-loads-at-base, relocated player, native self-start song), all
byte-exact. See the table in [format.md](format.md#byte-exact-verdict).

## Running locally

```bash
pip install -e ".[dev]"
export HVSC=/path/to/HVSC/C64Music          # optional; else fetched from a mirror
docker pull anarkiwi/sidtrace:latest
pytest -m oracle -n auto
```
