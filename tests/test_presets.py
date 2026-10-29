"""P. preset banks (the editor's P-SAVE PRESET): read, write, apply, round-trip."""

import pytest

from pymusicassembler import constants, player, reader, writer
from pymusicassembler.errors import MusicAssemblerError, MusicAssemblerParseError
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    Instrument,
    PresetBank,
    Song,
)


def test_default_bank_is_twelve_empty_slots_and_zero_presets():
    bank = PresetBank()
    assert bank.arpeggios == [None] * constants.ARP_COUNT
    assert len(bank.presets) == constants.PRESET_BANK_PRESETS
    assert all(p.to_bytes() == b"\x00" * 8 for p in bank.presets)
    assert bank.to_bytes() == b"\x00" * constants.PRESET_BANK_BODY


def test_slot_layout_and_padding():
    bank = PresetBank()
    bank.arpeggios[2] = Arpeggio(
        rows=[ArpeggioRow(0x41, 0x03, 0), ArpeggioRow(0x41, 0x05, 0)],
        terminator=constants.PROGRAM_STOP,
    )
    body = bank.to_bytes()
    assert (
        body[2 * 32 : 3 * 32]
        == bytes((0x41, 0x03, 0, 0x41, 0x05, 0, constants.PROGRAM_STOP)) + b"\x00" * 25
    )
    assert body[: 2 * 32] == b"\x00" * 64
    assert body[3 * 32 :] == b"\x00" * (constants.PRESET_BANK_BODY - 96)


def test_bank_round_trips():
    bank = PresetBank()
    bank.arpeggios[0] = Arpeggio(
        rows=[ArpeggioRow(1, 2, 3)], terminator=constants.PROGRAM_RESTART
    )
    bank.arpeggios[3] = Arpeggio(rows=[], terminator=constants.PROGRAM_STOP)
    bank.arpeggios[7] = Arpeggio(
        rows=[ArpeggioRow(0x41, 0, 0)], terminator=constants.PROGRAM_JUMP, jump=0
    )
    bank.arpeggios[15] = Arpeggio(
        rows=[ArpeggioRow(1, 2, 3)] * 10, terminator=constants.PROGRAM_STOP
    )
    bank.presets[5] = Instrument(1, 2, 3, 4, 5, 6, 7, 8)
    bank.presets[31] = Instrument.from_bytes(b"\x01" * 8)
    assert PresetBank.from_bytes(bank.to_bytes()) == bank


def test_row_interior_markers_are_data_and_slot_end_is_a_boundary():
    raw = bytearray(constants.PRESET_BANK_BODY)
    raw[32:35] = bytes((0x11, 0xFF, 0x00))  # a 0xFF note byte mid-row
    raw[35] = constants.PROGRAM_RESTART  # terminator at the next row start
    bank = PresetBank.from_bytes(bytes(raw))
    assert bank.arpeggios[1] == Arpeggio(
        rows=[ArpeggioRow(0x11, 0xFF, 0x00)], terminator=constants.PROGRAM_RESTART
    )

    raw = bytearray(constants.PRESET_BANK_BODY)
    raw[0:31] = bytes((0x10,) * 31)  # ten rows, then data where a terminator goes
    with pytest.raises(MusicAssemblerError, match="slot 0"):
        PresetBank.from_bytes(bytes(raw))


def test_jump_terminator_round_trips():
    bank = PresetBank()
    bank.arpeggios[0] = Arpeggio(
        rows=[ArpeggioRow(1, 2, 3)] * 10, terminator=constants.PROGRAM_JUMP, jump=7
    )
    back = PresetBank.from_bytes(bank.to_bytes())
    assert back.arpeggios[0] == bank.arpeggios[0]
    assert bank.to_bytes()[30:32] == bytes(
        (constants.PROGRAM_JUMP, 7)
    )  # $FD + target at the last row start of the slot


def test_to_bytes_rejects_bad_shapes():
    bank = PresetBank()
    bank.arpeggios = bank.arpeggios[:8]
    with pytest.raises(MusicAssemblerError, match="arpeggio"):
        bank.to_bytes()
    bank = PresetBank()
    bank.presets = bank.presets[:31]
    with pytest.raises(MusicAssemblerError, match="presets"):
        bank.to_bytes()


def test_to_bytes_rejects_program_that_does_not_fit_a_slot():
    bank = PresetBank()
    bank.arpeggios[0] = Arpeggio(rows=[ArpeggioRow(1, 0, 0)] * 11)
    with pytest.raises(MusicAssemblerError, match="slot 0"):
        bank.to_bytes()


def test_parse_presets_bare_body_and_load_prefixed():
    bank = PresetBank()
    bank.arpeggios[1] = Arpeggio(rows=[ArpeggioRow(1, 2, 3)])
    bank.presets[4] = Instrument.from_bytes(bytes(range(8)))
    body = bank.to_bytes()
    assert reader.parse_presets(body) == bank
    assert reader.parse_presets(bytes((0x00, 0x43)) + body) == bank
    with pytest.raises(MusicAssemblerParseError, match="preset bank"):
        reader.parse_presets(b"\x00" * 100)


def test_write_presets_emits_the_editor_form(tmp_path):
    bank = PresetBank()
    bank.presets[0] = Instrument.from_bytes(bytes(range(8)))
    out = writer.write_presets(bank, tmp_path / "bank.p")
    blob = out.read_bytes()
    assert blob[:2] == bytes((constants.PRESET_BANK_LOAD & 0xFF, 0x43))
    assert blob[2:] == bank.to_bytes()
    assert writer.build_presets(bank) == bank.to_bytes()


def test_apply_preset_bank_swaps_tables_by_copy():
    bank = PresetBank()
    bank.presets[0] = Instrument.from_bytes(bytes(range(8)))
    bank.arpeggios[4] = Arpeggio(rows=[ArpeggioRow(1, 2, 3)])
    song = Song(
        instruments=[Instrument.from_bytes(b"\xee" * 8)] * 2,
        inner_programs=[None] * constants.ARP_COUNT,
    )
    song.apply_preset_bank(bank)
    assert song.instruments == bank.presets
    assert song.inner_programs == bank.arpeggios
    bank.presets[1] = Instrument(9, 9, 9, 9, 9, 9, 9, 9)
    bank.arpeggios[4] = None
    assert song.instruments[1] == Instrument()
    assert song.inner_programs[4] is not None


def test_release_p_file_shape(preset_bank_path):
    blob = preset_bank_path.read_bytes()
    assert len(blob) == constants.PRESET_BANK_BODY + 2
    assert blob[:2] == bytes((constants.PRESET_BANK_LOAD & 0xFF, 0x43))
    bank = reader.read_presets(blob)
    assert bank.arpeggios[0] == Arpeggio(
        rows=[
            ArpeggioRow(0x81, 0xCF, 0),
            ArpeggioRow(0x11, 0xB3, 0),
            ArpeggioRow(0x10, 0xB0, 0),
            ArpeggioRow(0x10, 0xAD, 0),
            ArpeggioRow(0x10, 0xA8, 0),
            ArpeggioRow(0x10, 0xA2, 0),
            ArpeggioRow(0x10, 0x99, 0),
        ],
        terminator=constants.PROGRAM_STOP,
    )
    assert len(bank.arpeggios[5].rows) == 5
    assert bank.arpeggios[5].rows[1] == ArpeggioRow(0x11, 0xFF, 0x00)
    assert bank.arpeggios[5].terminator == constants.PROGRAM_RESTART
    assert all(a is None for a in bank.arpeggios[10:])
    assert bank.presets[2] == Instrument.from_bytes(bytes.fromhex("0e8e018c68521533"))
    assert all(p.to_bytes() == b"\x00" * 8 for p in bank.presets[15:])


def test_release_p_file_round_trips_byte_exact(preset_bank_path, tmp_path):
    blob = preset_bank_path.read_bytes()
    out = writer.write_presets(reader.read_presets(blob), tmp_path / "p.out")
    assert out.read_bytes() == blob


def test_release_presets_match_the_shape_song(preset_bank_path, shape_song_path):
    bank = reader.read_presets(preset_bank_path.read_bytes())
    song = reader.read(shape_song_path)
    assert bank.presets[2:15] == song.instruments[2:15]
    # The bank is a superset: presets 0-1 belong to other songs on the disk.
    assert bank.presets[0] != song.instruments[0]
    assert bank.presets[1] != song.instruments[1]


def test_apply_to_song_reemits_and_renders(preset_bank_path, shape_song_path):
    bank = reader.read_presets(preset_bank_path.read_bytes())
    song = reader.read(shape_song_path)
    song.apply_preset_bank(bank)
    assert song.instruments == bank.presets
    assert song.inner_programs == bank.arpeggios
    prg = reader.load_prefix(song.load_addr) + writer.build(song)
    re = reader.parse(prg)
    assert re.instruments == song.instruments
    assert re.inner_programs == song.inner_programs
    grid = player.render_grid(song, 50)
    assert len(grid) == 50 and len(grid[0]) == constants.SID_REG_COUNT
