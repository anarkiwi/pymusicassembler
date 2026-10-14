"""Stratified sample of a ``pysidtracker.survey`` run, by failure class.

``survey_sample.py SRC DST [--per-class N]`` copies whole tunes' cached oracle
traces per class into ``DST``; ``survey_sample.py DST --tally RESULTS`` prints
exact/total per class of a later ``results.csv``.
"""

import argparse
import csv
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from tqdm import tqdm


def _info(row):
    return json.loads(row["info"]) if row.get("info") else {}


def _roles(tri):
    return tri.get("roles", "").split() if tri.get("kind") == "value" else []


CLASSES = (
    ("not_decompiled", lambda r, i, t: r["verdict"] == "not_decompiled"),
    ("base_null", lambda r, i, t: i.get("load_offset", 0) is None),
    (
        "multispeed",
        lambda r, i, t: i.get("speed", 1) > 1 or i.get("trigger") == "cia_timer",
    ),
    ("subtune", lambda r, i, t: int(r["song"]) > 1),
    ("tempo", lambda r, i, t: i.get("tempo") not in (2, None)),
    ("res", lambda r, i, t: _roles(t) == ["res"]),
    ("fc", lambda r, i, t: "fc" in _roles(t)),
    ("ctrl", lambda r, i, t: _roles(t) == ["ctrl"]),
    ("exact", lambda r, i, t: r["verdict"] == "exact"),
    ("other", lambda r, i, t: True),
)


def classify(results, triage):
    """``{key: class}`` for every results row (first matching class wins)."""
    out = {}
    for row in results:
        info, tri = _info(row), triage.get(row["key"], {})
        out[row["key"]] = next(n for n, pred in CLASSES if pred(row, info, tri))
    return out


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sample(src, dst, per_class, seed):
    """Write the sample under ``dst``; return ``{key: class}`` for its subtunes."""
    src, dst = Path(src), Path(dst)
    tunes = {t["md5"]: t for t in _read(src / "tunes.csv")}
    triage = {t["key"]: t for t in _read(src / "triage.csv")}
    classes = classify(_read(src / "results.csv"), triage)
    by_class = defaultdict(set)
    for key, cls in classes.items():
        by_class[cls].add(key.rsplit("_", 1)[0])
    rng = random.Random(seed)
    chosen, taken = [], Counter()
    for cls, _ in CLASSES:
        md5s = sorted(by_class[cls] - set(chosen))
        rng.shuffle(md5s)
        for md5 in md5s:
            if taken[cls] >= per_class:
                break
            chosen.append(md5)
            taken[cls] += sum(
                1 for k, c in classes.items() if c == cls and k.startswith(md5)
            )
    (dst / "oracle").mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "oracle" / "STAMP", dst / "oracle" / "STAMP")
    keys = {}
    with open(dst / "tunes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(next(iter(tunes.values())).keys()))
        w.writeheader()
        for md5 in tqdm(chosen, unit="tune"):
            w.writerow(tunes[md5])
            for song in range(1, int(tunes[md5]["songs"]) + 1):
                key = f"{md5}_{song}"
                keys[key] = classes.get(key, "other")
                trace = src / "oracle" / f"{key}.csv.zst"
                if trace.exists():
                    shutil.copy(trace, dst / "oracle" / trace.name)
    with open(dst / "classes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "class"])
        w.writerows(sorted(keys.items()))
    return keys


def tally(classes_csv, results_csv):
    """``{class: [exact, total]}`` for a results CSV over a sample's classes."""
    classes = {r["key"]: r["class"] for r in _read(classes_csv)}
    out = defaultdict(lambda: [0, 0])
    for row in _read(results_csv):
        if row["key"] in classes:
            out[classes[row["key"]]][0] += row["verdict"] == "exact"
            out[classes[row["key"]]][1] += 1
    return dict(out)


def main(argv=None):
    """Sample, or with ``--tally`` report per-class exact counts."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="source survey work dir (sample dir with --tally)")
    ap.add_argument("dst", nargs="?", help="destination work dir")
    ap.add_argument("--per-class", type=int, default=30)
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--tally", help="results.csv to tally against SRC/classes.csv")
    args = ap.parse_args(argv)
    if args.tally:
        counts = tally(Path(args.src) / "classes.csv", args.tally)
        for cls, _ in CLASSES:
            if cls in counts:
                print(f"{cls:16} {counts[cls][0]:4d} / {counts[cls][1]:4d}")
        total = [sum(v[i] for v in counts.values()) for i in (0, 1)]
        print(f"{'total':16} {total[0]:4d} / {total[1]:4d}")
        return
    print(Counter(sample(args.src, args.dst, args.per_class, args.seed).values()))


if __name__ == "__main__":
    main()
