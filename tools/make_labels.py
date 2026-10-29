"""Per-second labels for a session, from its markers. Recording never changes when labelling does.

Writes <session>/features_labeled.csv: the features (features.csv, or a replay CSV with
--features) plus the protocol columns, from the condition codes in the session's protocol
(session.json, else config/protocol.yaml):
    condition, condition_name  the step the participant is in (code typed at its start)
    phase_idx                  1, 2, ... in order of the condition markers (repeats get new numbers)
    after                      name of the phase before (e.g. recovery after math or game)
    sr                         self-report sr:N given at the end of that phase
    is_baseline                1 for the seconds that set mu: the phase of the baseline source in
                               use at the end (after BASELINE_SKIP_S, c >= C_BASELINE)
A phase ends at its self-report: the seconds from sr:N to the next code (answering) are unlabelled.

--labels adds free-text columns from a YAML file of regex rules (see config/labels.example.yaml).
Examples:
    python tools/make_labels.py sessions/p01_20261029_154132
    python tools/make_labels.py sessions/p01_20261029_154132 --features replay_soft_smooth_gated_rest.csv
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402
from biofeedback.protocol import load_protocol  # noqa: E402

SR = re.compile(r"^sr:\s*(\d+)$", re.IGNORECASE)
PROTOCOL_COLUMNS = ["condition", "condition_name", "phase_idx", "after", "sr", "is_baseline"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def phases_from(markers, conditions):
    """[{code, name, idx, start, stop, sr, after}] from the condition, sr:N and end markers.
    stop = self-report or next code / end, whichever comes first (None = until the last row)."""
    phases = []
    for m in markers:
        text, t = m["text"].strip(), float(m["t_ms"])
        if text in conditions or text == "end":
            if phases and phases[-1]["stop"] is None:
                phases[-1]["stop"] = t
            if text in conditions:
                phases.append({"code": text, "name": conditions[text], "idx": len(phases) + 1, "start": t,
                               "stop": None, "sr": "", "after": phases[-1]["name"] if phases else ""})
        elif phases and SR.match(text) and not phases[-1]["sr"]:
            phases[-1]["sr"] = SR.match(text)[1]  # the phase that just ended (or is ending)
            if phases[-1]["stop"] is None:
                phases[-1]["stop"] = t
    return phases


def protocol_columns(rows, phases):
    source = rows[-1].get("baseline_source", "") if rows else ""
    for row in rows:
        t = float(row["t_ms"])
        phase = next((p for p in reversed(phases) if p["start"] <= t), None)
        if phase is None or (phase["stop"] is not None and t >= phase["stop"]):
            row.update({k: "" for k in PROTOCOL_COLUMNS})
            row["is_baseline"] = 0
            continue
        row.update({"condition": phase["code"], "condition_name": phase["name"], "phase_idx": phase["idx"],
                    "after": phase["after"], "sr": phase["sr"]})
        row["is_baseline"] = int(phase["name"] == source and t - phase["start"] >= S.BASELINE_SKIP_S * 1000
                                 and float(row["c"]) >= S.C_BASELINE
                                 and phase["idx"] == min(p["idx"] for p in phases if p["name"] == source))
    if source == "time":  # no condition markers: the seconds collected after the warm-up
        for row in rows:
            row["is_baseline"] = int(row["phase"] == "baseline" and float(row["c"]) >= S.C_BASELINE)


# ---- optional free-text columns (--labels) ----
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


def regex_columns(rows, markers, config):
    drop_after = config.get("drop_after_marker_s", 0) * 1000
    drop_before = config.get("drop_before_marker_s", 0) * 1000
    for name, spec in config["columns"].items():
        events = events_for(spec["rules"], markers)
        after = spec.get("drop_after_marker_s", drop_after / 1000) * 1000
        before = spec.get("drop_before_marker_s", drop_before / 1000) * 1000
        for row in rows:
            row[name] = label_at(float(row["t_ms"]), events, after, before)


def summary(rows, name):
    counts = {}
    for row in rows:
        key = str(row[name]) if row[name] != "" else "(none)"
        counts[key] = counts.get(key, 0) + 1
    return f"{name}: " + ", ".join(f"{k} {v} s" for k, v in sorted(counts.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="session folder (contains markers.csv and session.json)")
    ap.add_argument("--features", default="features.csv",
                    help="features file in the session folder (default features.csv; or a replay_*.csv)")
    ap.add_argument("--labels", help="optional YAML with extra free-text label columns")
    ap.add_argument("--out", help="output CSV (default <session>/features_labeled.csv)")
    args = ap.parse_args()

    session = Path(args.session)
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    protocol = meta.get("protocol") or load_protocol()
    conditions = {code: c["name"] for code, c in protocol["conditions"].items()}
    rows = read_csv(session / args.features)
    markers = read_csv(session / "markers.csv")

    phases = phases_from(markers, conditions)
    protocol_columns(rows, phases)
    for p in phases:
        stop = f"{p['stop'] / 1000:.0f} s" if p["stop"] is not None else "end"
        print(f"  phase {p['idx']}: {p['code']} {p['name']:9} {p['start'] / 1000:6.0f} s - {stop:>7}"
              f"  sr {p['sr'] or '-'}" + (f"  (after {p['after']})" if p["after"] else ""))
    print(summary(rows, "condition_name"))
    print(f"is_baseline: {sum(r['is_baseline'] for r in rows)} s "
          f"(baseline source in use: {rows[-1].get('baseline_source', '?') if rows else '-'})")
    if args.labels:
        config = yaml.safe_load(Path(args.labels).read_text(encoding="utf-8"))
        regex_columns(rows, markers, config)
        for name in config["columns"]:
            print(summary(rows, name))

    out = Path(args.out) if args.out else session / "features_labeled.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
