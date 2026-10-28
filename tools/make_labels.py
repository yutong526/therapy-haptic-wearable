"""Turn a session's free-text markers into per-second labels, using a labels.yaml file.

Writes <session>/features_labeled.csv: features.csv plus one column per entry in `columns`.
Recording never needs to change when the labelling scheme does. Example:
    python tools/make_labels.py sessions/p01_20261028_140000 --labels config/labels.example.yaml
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import yaml


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def events_for(rules, markers):
    """[(t_ms, label or None, hold_ms or None)] for the markers matched by these rules."""
    compiled = [(re.compile(r["match"], re.IGNORECASE), r) for r in rules]
    events = []
    for m in markers:
        for pattern, rule in compiled:
            hit = pattern.search(m["text"])
            if hit:
                label = rule.get("label")
                if label is not None:
                    label = str(label).format(hit.group(0), *hit.groups())
                hold = rule.get("hold_s")
                events.append((float(m["t_ms"]), label, None if hold is None else hold * 1000))
                break
    return sorted(events, key=lambda e: e[0])


def label_at(t, events, drop_after_ms, drop_before_ms):
    current = None
    following = None
    for e in events:
        if e[0] <= t:
            current = e
        else:
            following = e
            break
    if current is None or current[1] is None:
        return ""
    start, label, hold = current
    if t - start < drop_after_ms:
        return ""
    if hold is not None and t - start >= hold:
        return ""
    if following is not None and following[0] - t < drop_before_ms:
        return ""
    return label


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="session folder (contains features.csv and markers.csv)")
    ap.add_argument("--labels", required=True, help="labels.yaml")
    ap.add_argument("--out", help="output CSV (default <session>/features_labeled.csv)")
    args = ap.parse_args()

    session = Path(args.session)
    config = yaml.safe_load(Path(args.labels).read_text(encoding="utf-8"))
    features = read_csv(session / "features.csv")
    markers = read_csv(session / "markers.csv")
    drop_after = config.get("drop_after_marker_s", 0) * 1000
    drop_before = config.get("drop_before_marker_s", 0) * 1000

    columns = config["columns"]
    for name, spec in columns.items():
        events = events_for(spec["rules"], markers)
        after = spec.get("drop_after_marker_s", drop_after / 1000) * 1000
        before = spec.get("drop_before_marker_s", drop_before / 1000) * 1000
        for row in features:
            row[name] = label_at(float(row["t_ms"]), events, after, before)
        counts = {}
        for row in features:
            counts[row[name] or "(none)"] = counts.get(row[name] or "(none)", 0) + 1
        print(f"{name}: " + ", ".join(f"{k} {v} s" for k, v in sorted(counts.items())))

    out = Path(args.out) if args.out else session / "features_labeled.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(features[0].keys()) if features else [])
        writer.writeheader()
        writer.writerows(features)
    print(f"Saved {len(features)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
