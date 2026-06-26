"""Register log generation and serialization."""

import io

import pytest

from pymusicassembler import constants, reader
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)


def test_clock_layout(tune_path):
    writes = list(iter_register_writes(reader.read(tune_path), max_frames=2))
    first = writes[: constants.SID_REGISTERS]
    assert [w.reg for w in first] == list(range(constants.SID_REGISTERS))
    assert [w.clock for w in first] == [16 * n for n in range(25)]
    # Frame 1 starts one PAL frame later.
    second = writes[constants.SID_REGISTERS :]
    assert second[0].clock == constants.PAL_CYCLES_PER_FRAME


def test_clock_options(tune_path):
    writes = list(
        iter_register_writes(
            reader.read(tune_path),
            max_frames=2,
            cycles_per_frame=1000,
            write_spacing=2,
        )
    )
    assert writes[1].clock == 2
    assert writes[constants.SID_REGISTERS].clock == 1000


def test_bad_spacing(tune_path):
    with pytest.raises(MusicAssemblerError, match="write_spacing"):
        list(
            iter_register_writes(
                reader.read(tune_path), max_frames=1, cycles_per_frame=100
            )
        )


def test_write_read_path_round_trip(tune_path, tmp_path):
    writes = list(iter_register_writes(reader.read(tune_path), max_frames=60))
    path = tmp_path / "song.reglog"
    write_reglog(writes, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# pymusicassembler register log")
    assert read_reglog(path) == writes
    assert read_reglog(str(path)) == writes
    assert read_reglog(io.StringIO(text)) == writes
    with pytest.raises(TypeError):
        read_reglog(12345)


def test_write_stream_no_header():
    writes = [RegWrite(0, 24, 15), RegWrite(19656, 4, 0x41)]
    out = io.StringIO()
    write_reglog(writes, out, header=False)
    assert out.getvalue() == "0 24 15\n19656 4 65\n"
    assert read_reglog(io.StringIO(out.getvalue())) == writes


def test_read_blank_lines_and_comments():
    text = "# comment\n\n0 24 15  # trailing comment\n"
    assert read_reglog(io.StringIO(text)) == [RegWrite(0, 24, 15)]


@pytest.mark.parametrize("bad", ["0 24", "0 24 15 16", "a b c"])
def test_read_bad_lines(bad):
    with pytest.raises(MusicAssemblerError, match="line 1"):
        read_reglog(io.StringIO(bad))
