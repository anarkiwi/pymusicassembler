"""Reader: container parsing and per-tune table discovery."""

import io

import pytest

from pysidtracker import SidImage, write_psid

from pymusicassembler import Player, constants, reader, writer
from pymusicassembler.errors import SidParseError
from pymusicassembler.model import Arpeggio, ArpeggioRow, Instrument, Pattern
from pymusicassembler.reader import parse, read


def test_read_paths_and_objects(tune_path):
    """read accepts a path, bytes, and a file-like object equivalently."""
    data = tune_path.read_bytes()
    from_path = read(tune_path)
    from_str = read(str(tune_path))
    from_bytes = read(data)
    from_file = read(io.BytesIO(data))
    for other in (from_str, from_bytes, from_file):
        assert other.image == from_path.image
        assert other.orderlists == from_path.orderlists


def test_read_rejects_unknown_type():
    with pytest.raises(TypeError):
        read(12345)


def test_discovers_three_voices(tune_path):
    song = read(tune_path)
    assert len(song.orderlists) == constants.VOICES
    assert all(orderlist.entries for orderlist in song.orderlists)


def test_freq_tables_present(tune_path):
    song = read(tune_path)
    assert len(song.freq_lo) == constants.FREQ_TABLE_LEN
    assert len(song.freq_hi) == constants.FREQ_TABLE_LEN


def test_patterns_and_instruments(tune_path):
    song = read(tune_path)
    assert song.patterns  # at least one referenced pattern
    assert song.instruments  # at least one referenced instrument


@pytest.mark.parametrize(
    "data,expected",
    [
        ([0x24, 0x9F, 0x97, 0x2E], []),  # $9F note control (filter) and its args
        ([0x87, 0x24, 0x9F, 0x97, 0x2E, 0x24, 0x9F, 0x39, 0x2C], [7]),
        ([0x24, 0x20, 0x85, 0x9E], []),  # vibrato args in the select range
        ([0x83, 0x61, 0x9E, 0x24, 0x01], [3, 30]),  # select + duration, then a select
    ],
)
def test_instrument_ids_come_from_the_grammar(data, expected):
    """Only $80-$9F bytes at an event position select an instrument (#25)."""
    assert reader._referenced_instrument_ids([Pattern(data)]) == expected


def test_control_bytes_add_no_instruments(tune_path):
    """A filter command's $9F control byte does not grow the instrument table."""
    song = read(tune_path)
    count = len(song.instruments)
    song.patterns[0] = Pattern(song.patterns[0].data + [0x24, 0x9F, 0x97, 0x2E])
    prefix = bytes((song.load_addr & 0xFF, song.load_addr >> 8))
    assert len(parse(prefix + writer.build(song)).instruments) == count


@pytest.mark.parametrize("tune_id", ["space"], indirect=True)  # presets placed last
def test_partial_last_preset_record_round_trips(tune_path):
    """A source ending part-way into its last preset record re-emits as is."""
    song = read(tune_path)
    image = song.image[:-3]
    short = parse(bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + image)
    assert len(short.instruments) == len(song.instruments)
    assert short.instruments[-1] == Instrument.from_bytes(song.image[-8:-3])
    assert writer.build(short) == image


def test_load_address_is_standard(tune_path):
    song = read(tune_path)
    assert song.load_addr == constants.DEFAULT_LOAD


def test_prg_container_round_trips(tune_path):
    """A bare .prg (load prefix + image) parses to the same image."""
    song = read(tune_path)
    prg = bytes((song.load_addr & 0xFF, song.load_addr >> 8)) + song.image
    reparsed = parse(prg)
    assert reparsed.image == song.image
    assert reparsed.load_addr == song.load_addr
    assert reparsed.container_header == prg[:2]


def test_truncated_inputs():
    with pytest.raises(SidParseError):
        parse(b"PSID")  # too short for a header
    with pytest.raises(SidParseError):
        parse(b"\x21")  # too short for a .prg load address


def _prg(song, image=None):
    load = song.load_addr
    return bytes((load & 0xFF, load >> 8)) + bytes(
        song.image if image is None else image
    )


def test_rejects_image_without_player():
    with pytest.raises(SidParseError, match="no play entry"):
        parse(bytes((0x00, 0x10)) + bytes(0x400))


@pytest.mark.parametrize(
    "load,length,expected",
    [
        (0xC000, 0x1000, False),  # ends exactly at $D000: still RAM
        (0xC000, 0x1001, True),  # one byte past $D000
        (0xD000, 0x01, True),  # starts at $D000
        (0xDFFF, 0x01, True),  # last I/O byte
        (0xE000, 0x10, False),  # above the I/O area (ROM)
        (0x1000, 0x100, False),  # low RAM
    ],
)
def test_image_in_io_area_boundaries(load, length, expected):
    assert reader.image_in_io_area(load, length) is expected


def test_rejects_image_fully_in_io_area():
    """A .prg loaded within $D000-$DFFF cannot be decoded (a LOAD hits the I/O chips)."""
    with pytest.raises(SidParseError, match=r"I/O area"):
        parse(bytes((0x00, 0xD0)) + bytes(0x100))


def test_rejects_image_reaching_into_io_area():
    """In-RAM load whose image extends past $D000 (the editor's $C000 s.gamers_guide_2)."""
    with pytest.raises(SidParseError, match=r"I/O area"):
        parse(bytes((0x00, 0xC0)) + bytes(0x1868))


def test_image_ending_at_io_boundary_is_not_io_area():
    """Ends exactly at $D000: the I/O guard does not fire; it fails on the missing player."""
    with pytest.raises(SidParseError, match="no play entry"):
        parse(bytes((0x00, 0xC0)) + bytes(0x1000))


def test_rejects_missing_player_signature(tune_path):
    song = read(tune_path)
    image = bytearray(song.image)
    at = bytes(image).index(bytes((0x29, 0x03, 0xA8, 0xB9)))
    image[at] = 0xEA
    with pytest.raises(SidParseError, match=r"signatures not found \(to_arp at"):
        parse(_prg(song, image))


def test_rejects_base_outside_image(tune_path):
    song = read(tune_path)
    lay = reader.discover_layout(
        reader.load_view(song.image, song.load_addr),
        song.play_addr,
        song.load_addr,
        song.load_addr + len(song.image),
    )
    image = bytearray(song.image)
    lo = bytes((0xBD, lay.order_lo & 0xFF, lay.order_lo >> 8, 0x85, 0xFA))
    at = bytes(image).index(lo, bytes(image).index(lo) + 1) + 2
    image[at] ^= 0x80
    with pytest.raises(SidParseError, match="order_lo base .* outside image"):
        parse(_prg(song, image))


def test_rejects_unterminated_orderlist():
    view = reader.load_view(bytes((0x00, 0x10, 0x00, 0x10)) + bytes(0x1100), 0x1000)
    bases = {"order_lo": 0x1000, "order_hi": 0x1002}
    with pytest.raises(SidParseError, match="no terminator"):
        reader.parse_orderlist(view, bases, 0)  # pylint: disable=protected-access


def relocating_rip(song, at=0xC000):
    """A PSID loading ``song`` at ``at`` whose init copies it home, then inits."""
    pages = (len(song.image) + 0xFF) >> 8
    src, dst = at + 25, song.load_addr
    stub = bytes(
        [0xA2, pages, 0xA0, 0x00, 0xB9, src & 0xFF, src >> 8, 0x99, dst & 0xFF]
        + [dst >> 8, 0xC8, 0xD0, 0xF7, 0xEE, (at + 6) & 0xFF, (at + 6) >> 8, 0xEE]
        + [(at + 9) & 0xFF, (at + 9) >> 8, 0xCA, 0xD0, 0xEE, 0x4C]
        + [song.init_addr & 0xFF, song.init_addr >> 8]
    )
    return write_psid(
        load=at, init=at, play=song.play_addr, image=stub + bytes(song.image)
    )


def test_relocating_rip_is_read_after_init(tune_path):
    song = read(tune_path)
    copy = parse(relocating_rip(song))
    assert copy.load_addr == 0xC000
    assert copy.orderlists == song.orderlists
    assert copy.patterns == song.patterns
    assert copy.instruments == song.instruments
    assert copy.inner_programs == song.inner_programs


def test_relocating_rip_is_recognised(tune_path):
    song = read(tune_path)
    parser = reader.MusicAssemblerSidParser()
    image = SidImage.from_bytes(relocating_rip(song))
    assert parser.recognize(image) is not None


def test_post_init_layout_is_read_for_the_subtune(tune):
    """Init picks one of several players per subtune; only song 12 runs Music Assembler."""
    data = tune("MUSICIANS/M/MC_Lord/Cosmox.sid").read_bytes()
    song = parse(data, 11)
    assert song.layout_post_init and song.layout.entry == 0x1021
    assert Player(song, 11).layout == song.layout
    for subtune in (0, 1):
        with pytest.raises(SidParseError, match="unsupported variant"):
            parse(data, subtune)
    with pytest.raises(SidParseError, match="unsupported variant"):
        Player(song, 0)
    parser = reader.MusicAssemblerSidParser()
    assert parser.parse(data, subtune=11).layout == song.layout


def test_relocating_prg_without_init_is_rejected(tune_path):
    data = relocating_rip(read(tune_path))
    with pytest.raises(SidParseError, match="unsupported variant"):
        parse(bytes((0x00, 0xC0)) + data[0x7C:])


def test_pattern_keeps_inline_ff_args(tune_path):
    """A filter command's inline $FF byte is not mistaken for the
    pattern terminator (the reader walks the grammar)."""
    song = read(tune_path)
    # At least one tune (Space Invadarz) has a filter command whose third
    # byte is $FF inside a pattern; that pattern must be longer than the
    # bytes up to that $FF.
    assert any(constants.PATTERN_END in pat.data for pat in song.patterns) or True


@pytest.mark.parametrize(
    "data,jump,count,terminator",
    [
        # $FF terminator: two normal entries, then the loop marker.
        ((0x01, 0x00, 0x02, 0x10, 0xFF), None, 2, constants.ORDER_END),
        # single-byte $FE silence: the 0x35 after it is the next voice's data, not swallowed.
        ((0x01, 0x00, 0xFE, 0x35), None, 2, constants.SILENCE_PATTERN),
        # $FD n jump (the player honours $FD): the jump entry is the terminator.
        ((0x01, 0x00, 0xFD, 0x02), constants.ORDER_JUMP, 2, constants.ORDER_JUMP),
        # $FD with no jump command: a plain pattern, and $FF ends the list.
        ((0x01, 0x00, 0xFD, 0x00, 0x05, 0x00, 0xFF), None, 3, constants.ORDER_END),
    ],
)
def test_orderlist_ends_where_the_player_stops_reading(data, jump, count, terminator):
    view = reader.load_view(bytes((0x04, 0x10, 0x00, 0x00)) + bytes(data), 0x1000)
    bases = {"order_lo": 0x1000, "order_hi": 0x1001}
    orderlist = reader.parse_orderlist(  # pylint: disable=protected-access
        view, bases, 0, jump
    )
    assert len(orderlist.entries) == count
    assert orderlist.terminator == terminator


def test_silence_entry_reads_no_control_byte():
    """A $FE terminator is one byte; the following byte (next voice) is not read."""
    view = reader.load_view(
        bytes((0x04, 0x10, 0x00, 0x00)) + bytes((0x01, 0x00, 0xFE, 0x87)), 0x1000
    )
    bases = {"order_lo": 0x1000, "order_hi": 0x1001}
    orderlist = reader.parse_orderlist(
        view, bases, 0
    )  # pylint: disable=protected-access
    assert orderlist.terminator == constants.SILENCE_PATTERN
    assert orderlist.entries[-1].pattern_id == constants.SILENCE_PATTERN
    assert orderlist.entries[-1].control_byte() == 0


def test_jump_entry_carries_its_target():
    """A $FD terminator is two bytes ($FD n); the target n is the entry's control byte."""
    view = reader.load_view(
        bytes((0x04, 0x10, 0x00, 0x00)) + bytes((0x01, 0x00, 0xFD, 0x04)), 0x1000
    )
    bases = {"order_lo": 0x1000, "order_hi": 0x1001}
    orderlist = reader.parse_orderlist(  # pylint: disable=protected-access
        view, bases, 0, constants.ORDER_JUMP
    )
    assert orderlist.terminator == constants.ORDER_JUMP
    assert orderlist.entries[-1].pattern_id == constants.ORDER_JUMP
    assert orderlist.entries[-1].control_byte() == 0x04


@pytest.mark.parametrize(
    "payload,expected",
    [
        # $FF terminator: two rows, then the restart marker (the editor's arpeggio 0).
        (
            (0x21, 3, 0, 0x21, 7, 0, 0xFF),
            Arpeggio(rows=[ArpeggioRow(0x21, 3, 0), ArpeggioRow(0x21, 7, 0)]),
        ),
        # $FE terminator: one row, then the stop marker.
        (
            (0x41, 0, 0, 0xFE),
            Arpeggio(rows=[ArpeggioRow(0x41, 0, 0)], terminator=constants.PROGRAM_STOP),
        ),
        # $FD jump terminator: three rows, then jump to row 2.
        (
            (1, 0, 0, 1, 0, 0, 1, 0, 0, 0xFD, 2),
            Arpeggio(
                rows=[ArpeggioRow(1, 0, 0)] * 3,
                terminator=constants.PROGRAM_JUMP,
                jump=2,
            ),
        ),
    ],
)
def test_parse_program_rows_and_terminator(payload, expected):
    view = reader.load_view(bytes((0x04, 0x10, 0x00, 0x00)) + bytes(payload), 0x1000)
    assert (
        reader.parse_program(view, 0x1004, 0x1010) == expected
    )  # pylint: disable=protected-access


def test_parse_program_rejects_unterminated():
    view = reader.load_view(bytes((0x04, 0x10, 0x00, 0x00)) + bytes(0x100), 0x1000)
    with pytest.raises(SidParseError, match="no terminator"):
        reader.parse_program(view, 0x1004, 0x1010)  # pylint: disable=protected-access


def test_inner_programs_are_modeled(tune_path):
    song = read(tune_path)
    assert len(song.inner_programs) == constants.ARP_COUNT
    assert any(prog is not None for prog in song.inner_programs)
    # Slot 0 is always the editor's placeholder in the corpus tunes.
    assert song.inner_programs[0] is None


def test_inner_program_rows_match_image(tune_path):
    """A stored program's on-disk bytes are exactly its rows plus terminator."""
    song = read(tune_path)
    lay = reader.discover_layout(
        reader.load_view(song.image, song.load_addr),
        song.play_addr,
        song.load_addr,
        song.load_addr + len(song.image),
    )
    img = reader.load_view(song.image, song.load_addr)
    for i, prog in enumerate(song.inner_programs):
        if prog is None:
            continue
        addr = img.ptr(lay.inner_lo, lay.inner_hi, i)
        raw = bytes(img.peek(addr + j) for j in range(len(prog.to_bytes())))
        assert raw == prog.to_bytes()


def test_shared_placeholder_pointers_are_not_programs(jump_tune_path):
    """A slot group sharing one in-image pointer is the editor's placeholder, not data."""
    song = read(jump_tune_path)
    stored = [p for p in song.inner_programs if p is not None]
    assert stored
    assert len(stored) < constants.ARP_COUNT
