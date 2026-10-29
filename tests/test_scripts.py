"""The reverse-engineering and survey-sampling helpers under ``scripts/``."""

import csv
import json

import layout_mismatch
import native_roundtrip
import snapshot_probes
import survey_sample
from pysidtracker import load_prefix, survey

from pymusicassembler import cli, reader
from pymusicassembler.formats import MUSIC_ASSEMBLER


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _info(**kw):
    return json.dumps(dict({"load_offset": 0, "tempo": 2, "speed": 1}, **kw))


def test_sample_classes_copies_traces_and_tallies(tmp_path, capsys):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "oracle").mkdir(parents=True)
    (src / "oracle" / "STAMP").write_text("s")
    tunes, results, triage = [], [], []
    spec = {
        "a": ("differs", _info(tempo=3), {}),
        "b": ("differs", _info(load_offset=None), {}),
        "c": ("differs", _info(), {"kind": "value", "roles": "fc pw"}),
        "d": ("exact", _info(), {}),
        "e": ("not_decompiled", "", {}),
    }
    for md5, (verdict, info, tri) in spec.items():
        tunes.append(
            dict(
                path=f"{md5}.sid",
                md5=md5,
                player="p",
                songs=1,
                clock="PAL",
                lengths="1",
            )
        )
        results.append(dict(key=f"{md5}_1", song=1, verdict=verdict, info=info))
        triage.append(
            dict(key=f"{md5}_1", kind=tri.get("kind", ""), roles=tri.get("roles", ""))
        )
        (src / "oracle" / f"{md5}_1.csv.zst").write_bytes(b"x")
    _write(src / "tunes.csv", tunes)
    _write(src / "results.csv", results)
    _write(src / "triage.csv", triage)
    survey_sample.main([str(src), str(dst), "--per-class", "1"])
    capsys.readouterr()
    assert sorted(p.name for p in (dst / "oracle").iterdir()) == sorted(
        ["STAMP"] + [f"{m}_1.csv.zst" for m in spec]
    )
    classes = {f.stem: f.read_text().split() for f in (dst / "classes").iterdir()}
    assert classes == {
        "not_decompiled": ["e_1"],
        "base_null": ["b_1"],
        "tempo": ["a_1"],
        "fc": ["c_1"],
        "exact": ["d_1"],
    }
    results[0]["verdict"] = "exact"
    _write(tmp_path / "after.csv", results)
    survey.main(
        ["compare", str(tmp_path / "after.csv"), str(dst / "classes" / "tempo.txt")]
    )
    assert "tempo.txt: 1 {'exact': 1}" in capsys.readouterr().out


def test_native_roundtrip_reports_editor_files(editor_file, capsys):
    path = str(editor_file("s.parallax"))
    native_roundtrip.main([path])
    assert "build=identical native=identical: 1" in capsys.readouterr().out
    native_roundtrip.main([path, "--base", "$2000"])
    assert "native=same_music: 1" in capsys.readouterr().out


def test_native_roundtrip_labels_failures(tmp_path, jump_tune_path):
    junk = tmp_path / "junk.prg"
    junk.write_bytes(b"\x00\x10" + bytes(64))
    assert native_roundtrip.roundtrip(junk) == ("unread", "MusicAssemblerParseError")
    assert native_roundtrip.roundtrip(jump_tune_path)[1] == "rejected"


def test_layout_mismatch_names_the_differing_bytes(tmp_path, tune_path, capsys):
    data = tune_path.read_bytes()
    layout_mismatch.main([str(tune_path)])
    assert "matches" in capsys.readouterr().out
    patched = tmp_path / "patched.sid"
    patched.write_bytes(
        data.replace(bytes.fromhex("8D17D4A9FF"), bytes.fromhex("8C17D4A9FF"))
    )
    layout_mismatch.main([str(patched), "1"])
    out = capsys.readouterr().out
    assert "to_filter at" in out and ":8d>8c" in out
    patched.write_bytes(data.replace(bytes.fromhex("A200CE"), bytes.fromhex("A201CE")))
    layout_mismatch.main([str(patched)])
    assert "no play entry" in capsys.readouterr().out


def test_snapshot_probes(native_tune_path, capsys):
    """The relocated export rereads to the same music; the listing is the
    ``grammar`` subcommand's output."""
    tune = MUSIC_ASSEMBLER.load(native_tune_path.read_bytes())
    body = snapshot_probes.relocated(tune)
    again = reader.parse(load_prefix(snapshot_probes.RELOCATED_BASE) + body)
    assert again.orderlists == tune.song.orderlists
    assert again.patterns == tune.song.patterns
    assert snapshot_probes.layout(tune) is tune.song.layout
    assert cli.main(["grammar", str(native_tune_path)]) == 0
    listing = capsys.readouterr().out.splitlines()
    assert list(snapshot_probes.grammar(tune)) == listing
