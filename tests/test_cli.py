"""Command line interface."""

import pytest

from pymusicassembler import cli


def test_info(tune_path, capsys):
    assert cli.main(["info", str(tune_path)]) == 0
    out = capsys.readouterr().out
    assert "load:" in out
    assert "patterns:" in out
    assert "voice 0:" in out


def test_reglog(tune_path, tmp_path, capsys):
    out = tmp_path / "song.reglog"
    assert cli.main(["reglog", str(tune_path), str(out), "--seconds", "0.2"]) == 0
    assert out.exists()
    assert "wrote" in capsys.readouterr().out


def test_resave(tune_path, tmp_path):
    out = tmp_path / "out.sid"
    assert cli.main(["resave", str(tune_path), str(out)]) == 0
    assert out.read_bytes() == tune_path.read_bytes()


def test_resave_prg(tune_path, tmp_path):
    out = tmp_path / "out.prg"
    assert cli.main(["resave", str(tune_path), str(out), "--container", "prg"]) == 0
    assert out.exists()


def test_resave_native(tune_path, tmp_path):
    from pymusicassembler import reader

    out = tmp_path / "song.prg"
    assert cli.main(["resave", str(tune_path), str(out), "--container", "native"]) == 0
    blob = out.read_bytes()
    assert blob[2] == 0x78  # self-start stub follows the load-address prefix
    assert reader.read(out).patterns == reader.read(tune_path).patterns


def test_error_exit_code(tmp_path):
    missing = tmp_path / "nope.sid"
    assert cli.main(["info", str(missing)]) == 1


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
