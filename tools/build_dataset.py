"""Training samples from labelled sessions (docs/analysis_plan.md).

Input: session folders with features_labeled.csv (make_labels.py on a replay CSV). Output: one CSV,
one row per sample (every SAMPLE_EVERY_S, confidence >= MIN_SAMPLE_C, not in the first TRAIN_DROP_S
of a phase, and only once the final baseline is in use, so every z is relative to it as in actual
use; e.g. the chat phase itself and anything before it are left out), with the z features, the label of each map in config/label_maps.yaml (0 calm, 1 stress,
"test" = test-only condition, empty = not used) and the per-person train / test split of each phase
(first half train, second half test, CALIB_GAP_S or CALIB_GAP_CHECK_S between them).
Example:
    python tools/build_dataset.py sessions/20261030_Seona --out results/dataset.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
Z = [f"z_{k}" for k in S.FEATURES]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def split(t, first, last, gap_ms):
    """train / test / "" for a sample at t in a phase whose usable samples span first..last."""
    mid = (first + last) / 2
    if t < mid - gap_ms / 2:
        return "train"
    if t >= mid + gap_ms / 2:
        return "test"
    return ""


def session_samples(folder, maps, never):
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    rows = read_csv(folder / "features_labeled.csv")
    # only seconds scored against the final baseline, as in use (earlier z used a provisional mu)
    final = rows[-1]["baseline_source"] if rows else ""
    start = {}
    for r in rows:
        if r["phase_idx"]:
            start.setdefault(r["phase_idx"], float(r["t_ms"]))
    keep = []
    for r in rows:
        t = float(r["t_ms"])
        if not r["phase_idx"] or t - start[r["phase_idx"]] < S.TRAIN_DROP_S * 1000:
            continue
        if r["baseline_source"] != final:
            continue
        if int(t) % (S.SAMPLE_EVERY_S * 1000) or float(r["c"]) < S.MIN_SAMPLE_C or r["phase"] != "run":
            continue
        keep.append(r)
    span = {}
    for r in keep:
        t = float(r["t_ms"])
        a, b = span.get(r["phase_idx"], (t, t))
        span[r["phase_idx"]] = (min(a, t), max(b, t))
    out = []
    for r in keep:
        t, name = float(r["t_ms"]), r["condition_name"]
        sample = {"session": folder.name, "subject": meta.get("subject", ""), "t_ms": int(t),
                  "condition": name, "phase_idx": r["phase_idx"], "sr": r["sr"],
                  "is_baseline": r["is_baseline"], "c": r["c"],
                  **{z: 0.0 if r[z] in ("", "nan") else float(r[z]) for z in Z}}
        for m, spec in maps.items():
            sample[f"label_{m}"] = ("" if name in never else 0 if name in spec["calm"]
                                    else 1 if name in spec["stress"] else "test")
        first, last = span[r["phase_idx"]]
        sample["split30"] = split(t, first, last, S.CALIB_GAP_S * 1000)
        sample["split60"] = split(t, first, last, S.CALIB_GAP_CHECK_S * 1000)
        out.append(sample)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+", help="session folders (with features_labeled.csv)")
    ap.add_argument("--maps", default=str(ROOT / "config" / "label_maps.yaml"))
    ap.add_argument("--out", default=str(ROOT / "results" / "dataset.csv"))
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.maps).read_text(encoding="utf-8"))
    samples = []
    for s in args.sessions:
        folder = Path(s)
        if not (folder / "features_labeled.csv").exists():
            sys.exit(f"{folder}: no features_labeled.csv; run replay_session.py and make_labels.py first")
        rows = session_samples(folder, config["maps"], set(config.get("never_used", [])))
        counts = {}
        for r in rows:
            counts[r["condition"]] = counts.get(r["condition"], 0) + 1
        print(f"{folder.name}: {len(rows)} samples (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")")
        samples += rows
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
        w.writeheader()
        w.writerows(samples)
    print(f"Saved {len(samples)} samples to {out}")


if __name__ == "__main__":
    main()
