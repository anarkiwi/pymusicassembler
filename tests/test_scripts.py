"""The reverse-engineering and survey-sampling helpers under ``scripts/``."""

import csv
import json

import survey_sample


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
    results[0]["verdict"] = "exact"
    _write(tmp_path / "after.csv", results)
    survey_sample.main([str(dst), "--tally", str(tmp_path / "after.csv")])
    out = dict(line.split(None, 1) for line in capsys.readouterr().out.splitlines())
    assert out == {
        "not_decompiled": "0 /    1",
        "base_null": "0 /    1",
        "tempo": "1 /    1",
        "fc": "0 /    1",
        "exact": "1 /    1",
        "total": "2 /    5",
    }
