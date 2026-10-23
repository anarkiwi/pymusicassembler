"""Stratified sample of a ``pysidtracker.survey`` run, by failure class.

``survey_sample.py SRC DST [--per-class N]`` copies whole tunes' cached oracle
traces per class into ``DST`` and writes each class's subtune keys to
``DST/classes/<class>.txt``, so ``python -m pysidtracker.survey compare RESULTS
DST/classes/*.txt`` tallies a later ``results.csv`` per class.
"""

import argparse
import csv
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from pysidtracker.survey import load_tunes, read_results
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


def sample(src, dst, per_class, seed):
    """Write the sample under ``dst``; return ``{key: class}`` for its subtunes."""
    src, dst = Path(src), Path(dst)
    tunes = {t["md5"]: t for t in load_tunes(src / "tunes.csv")}
    triage = read_results(src / "triage.csv")
    classes = classify(read_results(src / "results.csv").values(), triage)
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
    by_key = defaultdict(list)
    for key, cls in sorted(keys.items()):
        by_key[cls].append(key)
    (dst / "classes").mkdir(exist_ok=True)
    for cls, members in by_key.items():
        (dst / "classes" / f"{cls}.txt").write_text("\n".join(members) + "\n", "utf-8")
    return keys


def main(argv=None):
    """Write the sample and print its subtune count per class."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="source survey work dir")
    ap.add_argument("dst", help="destination work dir")
    ap.add_argument("--per-class", type=int, default=30)
    ap.add_argument("--seed", type=int, default=15)
    args = ap.parse_args(argv)
    print(Counter(sample(args.src, args.dst, args.per_class, args.seed).values()))


if __name__ == "__main__":
    main()
