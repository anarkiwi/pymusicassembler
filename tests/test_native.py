"""Native Music Assembler editor song ("S." file) assembly and relocation."""

import pytest

from pymusicassembler import constants, native, player, reader, writer
from pymusicassembler.errors import SongValidationError
from pymusicassembler.model import Arpeggio, ArpeggioRow, OrderEntry, Pattern
from pysidtracker import SidImage

EDITOR_ORDER = ("space", "7years")  # sources already in the editor's table order


def _reread(base, body):
    return reader.parse(reader.load_prefix(base) + body)


def _layout(song):
    view = reader.load_view(song.image, song.load_addr)
    base = reader.find_player_base(view, song.load_addr, len(song.image))
    end = song.load_addr + len(song.image)
    return base, reader.discover_layout(view, base + 0x21, song.load_addr, end)


def _assert_same_music(a, b):
    assert a.orderlists == b.orderlists
    assert a.patterns == b.patterns
    assert a.instruments == b.instruments
    assert a.freq_lo == b.freq_lo
    assert a.freq_hi == b.freq_hi
    assert a.inner_programs == b.inner_programs


def test_native_body_opens_with_selfstart_stub(tune_path):
    """The body starts with the stub for its base, then the play routine at +$21."""
    base, body = native.assemble(reader.read(tune_path))
    assert body[: constants.NATIVE_STUB_LEN] == native.native_stub(base)
    play = body[constants.NATIVE_PLAY_OFFSET : constants.NATIVE_PLAY_OFFSET + 5]
    assert play[:3] == bytes((0xA2, 0x00, 0xCE))  # LDX #0; DEC tempo
    assert play[3] | (play[4] << 8) == base + constants.NATIVE_TEMPO_OFFSET


def test_native_base_matches_form(tune_path):
    """Base is the load address, or load-$21 for a rip that starts at play."""
    song = reader.read(tune_path)
    base, _ = native.assemble(song)
    assert song.load_addr - base in (0, constants.NATIVE_PLAY_OFFSET)


@pytest.mark.parametrize("base", [None, 0x2000, 0x8300])
def test_native_rereads_to_the_same_music(tune_path, base):
    """read(build_native(song, base)) reproduces the musical data at any base."""
    song = reader.read(tune_path)
    at, body = native.assemble(song, base)
    assert base is None or at == base
    _assert_same_music(_reread(at, body), song)


def test_native_uses_editor_table_order(tune_path):
    """Patterns, orderlists (voice 3 first), programs, pattern_hi, pattern_lo, presets."""
    base, body = native.assemble(reader.read(tune_path))
    out = _reread(base, body)
    _, lay = _layout(out)
    view = reader.load_view(out.image, base)
    patterns = [
        view.ptr(lay.pattern_lo, lay.pattern_hi, i) for i in range(len(out.patterns))
    ]
    orders = [view.ptr(lay.order_lo, lay.order_hi, v) for v in range(constants.VOICES)]
    programs = [
        view.ptr(lay.inner_lo, lay.inner_hi, i)
        for i, prog in enumerate(out.inner_programs)
        if prog is not None
    ]
    assert lay.order_hi + constants.VOICES <= min(patterns)
    assert max(patterns) < orders[2] < orders[1] < orders[0]
    assert orders[0] < min(programs) and max(programs) < lay.pattern_hi
    npat = len(out.patterns) + 1
    assert lay.pattern_lo == lay.pattern_hi + npat
    assert lay.instr == lay.pattern_lo + npat
    assert view.ptr(lay.pattern_lo, lay.pattern_hi, npat - 1) == 0


def test_native_export_is_stable(tune_path):
    """A native export re-assembles to itself byte-for-byte."""
    base, body = native.assemble(reader.read(tune_path))
    assert native.assemble(_reread(base, body)) == (base, body)


@pytest.mark.parametrize("tune_id", EDITOR_ORDER, indirect=True)
def test_native_plays_identically(tune_path):
    """An editor-order source plays frame-for-frame identically."""
    song = reader.read(tune_path)
    exported = _reread(*native.assemble(song))
    assert player.render_grid(exported, 400) == player.render_grid(song, 400)


@pytest.mark.parametrize("tune_id", ["7years"], indirect=True)
def test_relocated_native_plays_identically(tune_path):
    """A song whose program pitches stay in the 96-entry frequency table plays
    identically relocated (pitches past it read the pointer tables that move)."""
    song = reader.read(tune_path)
    exported = _reread(*native.assemble(song, 0x3000))
    assert player.render_grid(exported, 400) == player.render_grid(song, 400)


def test_native_relocates_every_player_operand(tune_path):
    """Relocating by $1000 moves each walked operand into the player by exactly $1000."""
    song = reader.read(tune_path)
    src, lay = _layout(song)
    base, body = native.assemble(song, src + 0x1000)
    view = reader.load_view(song.image, song.load_addr)
    code = native.walk_code(
        view, (lay.entry, lay.init), src + constants.NATIVE_STUB_LEN, lay.order_lo
    )
    moved = 0
    for pc, size in code.items():
        target = view.word(pc + 1)
        if size == 3 and src <= target < lay.order_lo:
            off = pc + 1 - src
            assert body[off] | body[off + 1] << 8 == target + base - src
            moved += 1
    assert moved > 100


def test_native_keeps_editor_files_byte_identical(native_tune_path, tmp_path):
    """A PSID-wrapped editor song exports back to its image byte-for-byte."""
    song = reader.read(native_tune_path)
    out = writer.write(song, tmp_path / "song.prg", container="native")
    expected = reader.load_prefix(song.load_addr) + song.image
    assert out.read_bytes() == expected


def test_build_native_matches_write(tune_path, tmp_path):
    """build_native is the body write() prefixes with its base load address."""
    song = reader.read(tune_path)
    base, body = native.assemble(song, 0x4000)
    assert writer.build_native(song, 0x4000) == body
    out = writer.write(song, tmp_path / "song.prg", container="native", base=0x4000)
    assert out.read_bytes() == reader.load_prefix(base) + body


def test_native_places_edits(tune_path):
    """Grown, added and emptied items re-read exactly."""
    song = reader.read(tune_path)
    song.patterns[0] = Pattern(song.patterns[0].data + [0x30, 0x01] * 20)
    song.patterns.append(Pattern([0x60]))
    song.orderlists[1].entries.append(OrderEntry(len(song.patterns) - 1))
    stored = [i for i, p in enumerate(song.inner_programs) if p is not None]
    song.inner_programs[stored[0]] = None
    free = next(i for i, p in enumerate(song.inner_programs) if p is None and i)
    song.inner_programs[free] = Arpeggio(rows=[ArpeggioRow(0x41, 0, 0)])
    _assert_same_music(_reread(*native.assemble(song)), song)


def test_native_rejects_nonstandard_layout(tune_path, monkeypatch):
    """A player with no play entry at base+$21 is not the editor's."""
    song = reader.read(tune_path)
    monkeypatch.setattr(reader, "find_player_base", lambda *args: None)
    with pytest.raises(SongValidationError, match="editor's layout"):
        writer.build_native(song)


def test_native_rejects_tables_out_of_editor_order(tune_path, monkeypatch):
    """Player pointer tables placed after the orderlist pointers are not the editor's."""
    song = reader.read(tune_path)
    real = reader.discover_layout

    def moved(*args):
        lay = real(*args)
        return type(lay)(**{**vars(lay), "inner_lo": lay.order_hi + 8})

    monkeypatch.setattr(reader, "discover_layout", moved)
    with pytest.raises(SongValidationError, match="editor's order"):
        writer.build_native(song)


@pytest.mark.parametrize("base", [0x0300, 0xFF01])
def test_native_rejects_base_outside_editor_range(tune_path, base):
    with pytest.raises(SongValidationError, match="outside the editor"):
        writer.build_native(reader.read(tune_path), base)


def test_native_refuses_io_base(tune_path):
    """A base whose song reaches $D000-$DFFF is not emitted (a C64 runs it as live I/O)."""
    with pytest.raises(SongValidationError, match=r"I/O area"):
        writer.build_native(reader.read(tune_path), 0xCC00)


def test_native_rejects_later_build(jump_tune_path):
    """A later build (init ahead of the play entry) is not the editor's player."""
    with pytest.raises(SongValidationError, match="editor's layout"):
        writer.build_native(reader.read(jump_tune_path))


def test_native_rejects_orderlist_jump(tune_path):
    song = reader.read(tune_path)
    song.orderlists[0].terminator = constants.ORDER_JUMP
    with pytest.raises(SongValidationError, match=r"orderlist \$FC/\$FD"):
        writer.build_native(song)


def test_native_rejects_program_jump(tune_path):
    song = reader.read(tune_path)
    slot = next(i for i, p in enumerate(song.inner_programs) if p is not None)
    song.inner_programs[slot].terminator = constants.PROGRAM_JUMP
    with pytest.raises(SongValidationError, match=r"program \$FD"):
        writer.build_native(song)


def test_native_rejects_operand_into_unplaced_data(tune_path, monkeypatch):
    """A player operand into song data the layout does not place cannot be relocated."""
    song = reader.read(tune_path)
    _, lay = _layout(song)
    stray = song.load_addr + len(song.image) - 1
    real = native.walk_code

    def with_stray(image, *args):
        code = real(image, *args)
        image.mem[lay.entry + 1 : lay.entry + 3] = reader.load_prefix(stray)
        return {**code, lay.entry: 3}

    monkeypatch.setattr(native, "walk_code", with_stray)
    with pytest.raises(SongValidationError, match="does not place"):
        native.assemble(song)


def test_order_hook_build_exports_in_editor_order(tune):
    """Kasza-Nota (order-hook build, presets ahead of patterns) exports in the
    editor's order, keeping the hook routine after the orderlist pointers."""
    song = reader.read(tune("MUSICIANS/A/ASL/Kasza-Nota.sid"))
    with pytest.raises(SongValidationError, match=r"\[2\] exceed .* 32-byte"):
        native.assemble(song)  # its 34-byte program 2 overruns the editor's slot
    song.inner_programs[2].rows.pop()
    out = _reread(*native.assemble(song))
    _assert_same_music(out, song)
    _, lay = _layout(out)
    assert lay.order_jump and lay.pattern_hi < lay.pattern_lo < lay.instr


def _image(code: bytes, at: int = 0x1000) -> SidImage:
    return SidImage.from_prg(reader.load_prefix(at) + code)


@pytest.mark.parametrize(
    "code,expected",
    [
        # JSR $1006; RTS; data; LDA $1234; RTS
        (
            bytes.fromhex("200610600000ad341260"),
            {0x1000: 3, 0x1003: 1, 0x1006: 3, 0x1009: 1},
        ),
        # CLC; BCC +1 is taken, so the JAM after it is never walked.
        (bytes.fromhex("18900102ea60"), {0x1000: 1, 0x1001: 2, 0x1004: 1, 0x1005: 1}),
        # BNE +1 falls through into a JAM, which ends that path only.
        (bytes.fromhex("d00102ea60"), {0x1000: 2, 0x1002: 1, 0x1003: 1, 0x1004: 1}),
        # JMP $1005 skips data; the target RTS ends the path.
        (bytes.fromhex("4c0510ffff60"), {0x1000: 3, 0x1005: 1}),
    ],
)
def test_walk_code_follows_control_flow(code, expected):
    end = 0x1000 + len(code)
    assert native.walk_code(_image(code), [0x1000], 0x1000, end) == expected


def test_walk_code_stays_in_range():
    """Targets outside [lo, hi) are not walked."""
    code = bytes.fromhex("20ffcf60")  # JSR $CFFF; RTS
    assert native.walk_code(_image(code), [0x1000], 0x1000, 0x1004) == {
        0x1000: 3,
        0x1003: 1,
    }


def test_native_keeps_unread_patterns_unread(tune):
    """A song with an unplayed, unterminated pattern id rereads to the same music."""
    song = reader.read(tune("MUSICIANS/F/Feekzoid/Missing_DigiSid_2_Trash_City.sid"))
    assert None in song.patterns
    out = _reread(
        song.layout.entry - constants.NATIVE_PLAY_OFFSET, writer.build_native(song)
    )
    assert out.orderlists == song.orderlists
    assert [p and p.data for p in out.patterns] == [p and p.data for p in song.patterns]
    assert out.inner_programs == song.inner_programs
