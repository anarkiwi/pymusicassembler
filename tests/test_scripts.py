"""The reverse-engineering and survey-sampling helpers under ``scripts/``."""

import csv
import json

import survey_sample
from pysidtracker import survey


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
