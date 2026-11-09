"""Control test (config/control_test.yaml): does the airbag help? Airbag block vs control block.

Each session has two blocks: a task (math or English) plus the rest after it, one with the closed-loop
airbag allowed, one without (control: the same rule only logs when it would have started the pump,
"would_on" in pump.csv). Blocks come from the notes (note:math / note:english ... note:end, the rest
until the next task note) or, for protocols with airbag: true steps, from the steps. Both are aligned on that trigger time (t = 0: the pump started, or would
have), so the airbag and the control are compared at the same point of the stress response:
  - latency: trigger time after the task start
  - arousal, HR, EDA level: mean of the 30 s before the trigger, of 0-60 s and 60-120 s after it,
    and the change (60-120 s after minus 30 s before)
  - recovery: seconds from the trigger until arousal first falls below RECOVERED_AROUSAL
  - task / rest means of arousal, HR and EDA, valid share, seconds the pump was on
Saves <session>/control_blocks.csv and control_overview.png (both blocks aligned on the trigger);
with several sessions also results/control_summary.csv and the mean airbag - control differences.
A few sessions describe these runs; they are not a tested effect.
Example:
    python tools/analyze_control.py sessions/20261106_p01 sessions/20261106_p02
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback.live_window import Progress  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BEFORE_S = 30
AFTER_WINDOWS = [(0, 60), (60, 120)]
RECOVERED_AROUSAL = 0.5
PLOT_S = (-60, 180)
KEYS = ["arousal", "hr", "eda_level"]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nanmean(values):
    values = [v for v in values if v == v]
    return sum(values) / len(values) if values else math.nan


def window(features, key, a_s, b_s):
    return nanmean([num(r[key]) for r in features if a_s * 1000 < num(r["t_ms"]) <= b_s * 1000])


def steps_with_times(meta, markers, end_s):
    """[(index, code, start_s, end_s, airbag)] of the steps entered, from the condition markers."""
    protocol, order = meta["protocol"], meta["order"]
    tracker = Progress(protocol, order, meta.get("warmup_s") or protocol["settling_s"])
    starts = []
    for m in markers:
        t, text = num(m["t_ms"]) / 1000, m["text"].strip()
        before = tracker.index
        tracker.marker(t, text)
        if text in protocol["conditions"] and tracker.index != before:
            starts.append((tracker.index, t))
    out = []
    for k, (i, t) in enumerate(starts):
        step = protocol["orders"][order][i]
        out.append((i, step["code"], t, starts[k + 1][1] if k + 1 < len(starts) else end_s,
                    bool(step.get("airbag", False))))
    return out


DEMO_TASKS = ["math", "english", "game"]  # task notes in demo / therapy sessions (airbag allowed in all)


def note_blocks(meta, markers, end_s):
    """[(task, start_s, task_end_s, block_end_s, airbag)] from note:<task> ... note:end markers. Demo
    sessions (no "control" in the protocol): every task block had the airbag allowed."""
    control = meta["protocol"].get("control") or {"tasks": DEMO_TASKS, "airbag_task": None}
    airbag_task = control["airbag_task"][meta["order"]] if control["airbag_task"] else None
    notes = [(num(m["t_ms"]) / 1000, m["text"].strip()[5:].strip().lower()) for m in markers
             if m["text"].strip().lower().startswith("note:")]
    starts = [(t, x) for t, x in notes if x in control["tasks"]]
    out = []
    for k, (t, task) in enumerate(starts):
        block_end = starts[k + 1][0] if k + 1 < len(starts) else end_s
        task_end = next((u for u, x in notes if x == "end" and t < u <= block_end), block_end)
        out.append((task, t, task_end, block_end, airbag_task is None or task == airbag_task))
    return out


def step_blocks(meta, markers, end_s):
    """The same from the steps (airbag: true per step), a task step plus the rest step after it."""
    conditions = meta["protocol"]["conditions"]
    steps = steps_with_times(meta, markers, end_s)
    out = []
    for k, (i, code, a, b, airbag) in enumerate(steps):
        name = conditions[code]["name"]
        if name in ("chat", "rest"):
            continue
        rest = steps[k + 1] if k + 1 < len(steps) and conditions[steps[k + 1][1]]["name"] == "rest" else None
        out.append((name, a, b, rest[3] if rest else b, airbag))
    return out


def analyze(folder):
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    features = read_csv(folder / "features.csv")
    markers = read_csv(folder / "markers.csv")
    pump = read_csv(folder / "pump.csv") if (folder / "pump.csv").exists() else []
    end_s = num(features[-1]["t_ms"]) / 1000
    by_steps = any(st.get("airbag") for st in meta["protocol"]["orders"][meta["order"]])
    specs = (step_blocks if by_steps else note_blocks)(meta, markers, end_s)
    blocks = []
    for name, a, b, block_end, airbag in specs:
        rest = block_end > b
        if airbag:
            starts = [num(p["t_ms"]) / 1000 for p in pump
                      if p["on"] == "1" and not p["reason"].startswith("manual")]
        else:
            starts = [num(p["t_ms"]) / 1000 for p in pump if p.get("mode") == "would_on"]
        trig = next((t for t in starts if a <= t < block_end), None)
        on_s = 0.0  # seconds the pump was really on within the block
        on_at = None
        for p in pump:
            t = num(p["t_ms"]) / 1000
            if p.get("mode") in ("would_on", "would_off"):
                continue
            if p["on"] == "1" and on_at is None:
                on_at = t
            elif p["on"] == "0" and on_at is not None:
                on_s += max(0.0, min(t, block_end) - max(on_at, a))
                on_at = None
        if on_at is not None:
            on_s += max(0.0, block_end - max(on_at, a))
        row = {"session": folder.name, "order": meta["order"], "condition": "airbag" if airbag else "control",
               "task": name, "task_start_s": round(a), "trigger_s": round(trig) if trig else "",
               "latency_s": round(trig - a) if trig else "", "pump_on_s": round(on_s),
               "valid_share": round(window(features, "valid", a, block_end), 2)}
        for key in KEYS:
            row[f"task_{key}"] = round(window(features, key, a, b), 2)
            row[f"rest_{key}"] = round(window(features, key, b, block_end), 2) if rest else ""
            if trig:
                before = window(features, key, trig - BEFORE_S, trig)
                row[f"{key}_before"] = round(before, 2)
                for w0, w1 in AFTER_WINDOWS:
                    row[f"{key}_{w0}_{w1}s"] = round(window(features, key, trig + w0, trig + w1), 2)
                last = AFTER_WINDOWS[-1]
                row[f"{key}_change"] = round(window(features, key, trig + last[0], trig + last[1]) - before, 2)
        if trig:
            rec = next((num(r["t_ms"]) / 1000 - trig for r in features
                        if num(r["t_ms"]) / 1000 >= trig and num(r["arousal"]) < RECOVERED_AROUSAL), None)
            row["recovery_s"] = round(rec) if rec is not None else ""
        blocks.append(row)
    return meta, features, blocks


def overview(folder, features, blocks):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"airbag": "#2E7D32", "control": "#757575"}
    fig, axes = plt.subplots(len(KEYS), 1, figsize=(9, 8), sharex=True, gridspec_kw={"hspace": 0.35})
    titles = {"arousal": "Arousal (0-1)", "hr": "Heart rate (bpm)", "eda_level": "EDA level (uS)"}
    for ax, key in zip(axes, KEYS):
        for b in blocks:
            if b["trigger_s"] == "":
                continue
            trig = b["trigger_s"]
            pts = [(num(r["t_ms"]) / 1000 - trig, num(r[key])) for r in features
                   if PLOT_S[0] <= num(r["t_ms"]) / 1000 - trig <= PLOT_S[1]]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], lw=1.8, color=colors[b["condition"]],
                    label=f"{b['condition']} ({b['task']})" + (" - pump on" if b["condition"] == "airbag"
                                                              else " - pump would start"))
        ax.axvline(0, color="#333333", lw=1, ls="--")
        ax.set_title(titles[key], loc="left", fontsize=10)
        ax.grid(alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(loc="upper right", fontsize=8, frameon=False)
    axes[-1].set_xlabel("seconds from the trigger (pump started / would have started)")
    fig.suptitle(f"{folder.name}: airbag vs control, aligned on the trigger", x=0.01, ha="left", fontsize=11)
    path = folder / "control_overview.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def write_csv(path, rows):
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, columns)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+")
    args = ap.parse_args()
    all_rows, diffs = [], []
    for s in args.sessions:
        folder = Path(s)
        meta, features, blocks = analyze(folder)
        if not blocks:
            print(f"{folder.name}: no task steps found (protocol {meta.get('protocol_file')})")
            continue
        write_csv(folder / "control_blocks.csv", blocks)
        print(f"\n== {folder.name} (order {meta['order']})")
        for b in blocks:
            trig = (f"trigger +{b['latency_s']} s, recovery {b.get('recovery_s') or '-'} s" if b["trigger_s"] != ""
                    else "no trigger")
            changes = "  ".join(f"{k} {b[f'{k}_before']}->{b[f'{k}_60_120s']}" for k in KEYS if f"{k}_before" in b)
            print(f"  {b['condition']:7} {b['task']:8} {trig:34} pump on {b['pump_on_s']:>4} s | {changes}")
        print(f"  saved {folder / 'control_blocks.csv'} and {overview(folder, features, blocks)}")
        all_rows += blocks
        by = {b["condition"]: b for b in blocks}
        if "airbag" in by and "control" in by:
            diffs.append({k: num(by["airbag"].get(k)) - num(by["control"].get(k))
                          for k in ["recovery_s"] + [f"{k}_change" for k in KEYS]})
    if len(args.sessions) > 1:
        out = ROOT / "results" / "control_summary.csv"
        out.parent.mkdir(exist_ok=True)
        write_csv(out, all_rows)
        print(f"\nsaved {out}")
    if diffs:
        print(f"\nairbag - control, mean over {len(diffs)} session(s) (negative = lower / faster with the airbag):")
        for k in diffs[0]:
            print(f"  {k:18} {nanmean([d[k] for d in diffs]):+.2f}")


if __name__ == "__main__":
    main()
