"""Figures and numbers for the final presentation (analysis only; reads sessions/, writes analysis_out/).

    .venv\\Scripts\\python analysis_out\\make_figures.py
"""

import csv
import json
import math
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "sessions"
OUT = ROOT / "analysis_out"
TEAL, ORANGE, NAVY, GREY = "#267F92", "#C7793D", "#172F49", "#9AA5AE"
SIZE = (10, 4.5)
CLOSED_LOOP = ["20261105_A", "20261105_B", "20261105_C_3", "20261105_D"]
INFLATED_KPA = 0.5  # a pump-on period "inflated" if the pressure reached at least this
EDA_MISSING_FROM = {"20261105_D": 878}  # BITalino battery died: EDA / ACC invalid from here (user note)

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"], "font.size": 12,
    "axes.labelsize": 12, "xtick.labelsize": 11, "ytick.labelsize": 11, "legend.fontsize": 11,
    "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#555555",
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
})


def sdir(name):
    """sessions/<name>; names are pseudonyms, mapped to the local folder by private/pseudonym_map.csv
    (real,pseudonym; not in git) when the folder itself still has the real name."""
    if not (S / name).exists():
        mp = ROOT / "private" / "pseudonym_map.csv"
        real = {r["pseudonym"]: r["real"] for r in read_csv(mp)} if mp.exists() else {}
        if name in real:
            return S / real[name]
    return S / name


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)


def nanmean(a):
    a = np.asarray(a, float)
    return float(np.nanmean(a)) if np.isfinite(a).any() else math.nan


def save(fig, name):
    fig.savefig(OUT / name, dpi=300, bbox_inches="tight")
    plt.close(fig)


def esp32(folder):
    """P lines (t_s laptop, kPa, target, pwm_in, pwm_out, phase) and F lines (t_s laptop, board_s, phase)."""
    P, F, H = [], [], []
    for r in read_csv(folder / "esp32_log.csv"):
        t, line = num(r["t_ms"]) / 1000, r["line"]
        parts = line.split(",")
        if parts[0] == "P" and len(parts) >= 7:
            P.append((t, num(parts[2]), num(parts[3]), num(parts[4]), num(parts[5]), parts[6]))
        elif parts[0] == "F" and len(parts) >= 3:
            F.append((t, num(parts[1]) / 1000, parts[2]))
        elif line.startswith("#"):
            H.append((t, line))
    return P, F, H


def pump_periods(folder):
    """[(on_s, off_s, start_reason, stop_reason)] of real pump-on periods; would_on rows separately."""
    rows = read_csv(folder / "pump.csv")
    periods, would, on = [], [], None
    for r in rows:
        t = num(r["t_ms"]) / 1000
        if r["mode"] == "would_on":
            would.append((t, r["reason"]))
        elif r["mode"] == "would_off":
            continue
        elif r["on"] == "1" and on is None:
            on = (t, r["reason"])
        elif r["on"] == "0" and on is not None:
            periods.append((on[0], t, on[1], r["reason"]))
            on = None
    if on is not None:
        periods.append((on[0], math.nan, on[1], "(still on at end)"))
    return periods, would


def blocks(folder):
    """Task blocks from notes: [(task, start_s, end_s)]."""
    notes = [(num(m["t_ms"]) / 1000, m["text"].strip()[5:].strip().lower())
             for m in read_csv(folder / "markers.csv") if m["text"].strip().lower().startswith("note:")]
    out = []
    for i, (t, x) in enumerate(notes):
        if x in ("math", "english", "game"):
            end = next((u for u, y in notes[i + 1:] if y == "end"), math.nan)
            out.append((x, t, end))
    return out


def phase_at(folder, t):
    bl = blocks(folder)
    for task, a, b in bl:
        if a <= t < b:
            return f"task ({task})"
    if bl and t < bl[0][1]:
        return "before first task"
    prev = [task for task, a, b in bl if b <= t]
    return f"rest after {prev[-1]}" if prev else "rest"


def eda_1hz(name):
    """1-s means of raw EDA (uS): (t_s array of bin starts, values)."""
    t, v = [], []
    with open(sdir(name) / "eda.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t.append(num(r["t_ms"]) / 1000)
            v.append(num(r["eda_us"]))
    t, v = np.array(t), np.array(v)
    k = np.floor(t).astype(int)
    n = k.max() + 1
    s = np.bincount(k, weights=np.nan_to_num(v), minlength=n)
    c = np.bincount(k, weights=np.isfinite(v).astype(float), minlength=n)
    with np.errstate(invalid="ignore"):
        out = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    if name in EDA_MISSING_FROM:
        out[EDA_MISSING_FROM[name]:] = np.nan
    return np.arange(n), out


# ---------------------------------------------------------------- A
def task_a(md):
    folder = sdir("20261105_A")
    P, F, H = esp32(folder)
    periods, _ = pump_periods(folder)
    rows = []
    fig, axes = plt.subplots(1, len(periods), figsize=SIZE, sharey=True)
    for i, (on, off, why, stop) in enumerate(periods):
        p = [x for x in P if on <= x[0] <= off]
        kpa = [x[1] for x in p]
        inh = [x for x in F if on <= x[0] < off and x[2] == "INHALE"]
        defl = next((x for x in F if x[0] >= off - 0.5 and x[2] == "DEFLATE"), None)
        idle = next((x for x in F if defl and x[0] > defl[0] and x[2] == "IDLE"), None)
        pwm_in = sorted({int(x[3]) for x in p if x[5] == "INHALE"})
        rows.append({"period": i + 1, "on_s": round(on), "off_s": round(off), "on_duration_s": round(off - on),
                     "max_kPa": round(max(kpa), 2), "min_kPa": round(min(kpa), 2),
                     "n_INHALE": len(inh), "pwm_in_during_INHALE": "/".join(map(str, pwm_in)),
                     "deflate_s": round(idle[0] - defl[0], 1) if idle else "",
                     "inflated": "yes" if max(kpa) >= INFLATED_KPA else "no",
                     "n_P_lines": len(p)})
        ax = axes[i]
        ax.plot([x[0] - on for x in p], kpa, color=TEAL if max(kpa) >= INFLATED_KPA else GREY, lw=1.4)
        for y in (3, -1):
            ax.axhline(y, color=NAVY, lw=1, ls="--")
        ax.set_xlabel(f"seconds from pump start ({round(on)} s)")
        ax.set_ylim(-1.3, 3.9)
        ax.text(0.02, 0.99, f"period {i + 1}: max {max(kpa):.2f} kPa\n{len(inh)} inhales", transform=ax.transAxes,
                va="top", fontsize=11, color=NAVY)
    axes[0].set_ylabel("airbag pressure (kPa)")
    save(fig, "A_pump_pressure_participantA.png")
    write_csv(OUT / "A_pump_periods_participantA.csv", rows)
    mk = [(num(m["t_ms"]) / 1000, m["text"]) for m in read_csv(folder / "markers.csv")]
    header_times = sorted({round(t) for t, _ in H})
    md.append("## A. Participant A pump check (session 20261105_A, demo)\n")
    md.append("Source: `esp32_log.csv` (P lines: pressure every 0.1 s while on, 1 s while idle; F lines: phase "
              "changes) and `pump.csv`. Figure `A_pump_pressure_participantA.png`, table `A_pump_periods_participantA.csv`.\n")
    md.append("| period | on (s) | off (s) | on for (s) | max kPa | min kPa | INHALE phases | PWM in INHALE | DEFLATE took (s) | bag inflated |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        md.append(f"| {r['period']} | {r['on_s']} | {r['off_s']} | {r['on_duration_s']} | {r['max_kPa']} | {r['min_kPa']} | "
                  f"{r['n_INHALE']} | {r['pwm_in_during_INHALE']} | {r['deflate_s']} | {r['inflated']} |")
    md.append("")
    md.append(f"- Board header lines (`# mode ...`) at t = {header_times} s: every pump start re-sent the same setup "
              "(mode breathing, inhale PWM 255 to 3 kPa, exhale PWM 120 to -1 kPa, max 8 kPa, motors on). "
              "No fault lines, no restart and no `ERR`/safety message in the log.")
    md.append(f"- Markers: {', '.join(f'{t:.0f} s `{x}`' for t, x in mk)}.")
    return rows


# ---------------------------------------------------------------- B
def cycles(P, F, on, off):
    """Per breathing cycle (INHALE start to the next INHALE start, inside [on, off]): length, peak, minimum."""
    inh = [x for x in F if on <= x[0] < off and x[2] == "INHALE"]
    out = []
    for a, b in zip(inh, inh[1:]):
        p = [x[1] for x in P if a[0] <= x[0] < b[0]]
        ex = next((x for x in F if a[0] < x[0] < b[0] and x[2] == "EXHALE"), None)
        out.append({"inhale_start_s": round(a[0], 1), "cycle_s": round(b[1] - a[1], 2),
                    "inhale_s": round(ex[1] - a[1], 2) if ex else "", "peak_kPa": max(p), "min_kPa": min(p)})
    return out


def task_b(md):
    name = "20261105_B"
    folder = sdir(name)
    P, F, _ = esp32(folder)
    periods, _ = pump_periods(folder)
    allc = []
    for k, (on, off, *_r) in enumerate(periods):
        for c in cycles(P, F, on, off):
            allc.append({"session": name, "period": k + 1, **c})
    write_csv(OUT / "B_cycles_participantB.csv", allc)
    # 60 s figure from the longest period, starting at its 3rd inhale (past the first fill)
    on, off = max(((a, b) for a, b, *_r in periods), key=lambda x: x[1] - x[0])
    inh = [x for x in F if on <= x[0] < off and x[2] == "INHALE"]
    t0 = inh[2][0]
    t1 = t0 + 60
    fig, ax = plt.subplots(figsize=SIZE)
    phases = [x for x in F if t0 - 15 <= x[0] <= t1 + 15]
    for a, b in zip(phases, phases[1:]):
        if a[2] in ("INHALE", "EXHALE"):
            ax.axvspan(max(a[0], t0) - t0, min(b[0], t1) - t0, color=TEAL if a[2] == "INHALE" else ORANGE,
                       alpha=0.13, lw=0)
    p = [x for x in P if t0 <= x[0] <= t1]
    ax.plot([x[0] - t0 for x in p], [x[1] for x in p], color=NAVY, lw=1.8)
    for y, lab in ((3, "inhale stop 3 kPa"), (-1, "exhale stop -1 kPa")):
        ax.axhline(y, color=GREY, lw=1.2, ls="--")
        ax.text(60.5, y, lab, va="center", fontsize=11, color="#555555")
    ax.set_xlim(0, 60)
    ax.set_xlabel(f"time (s), from {t0:.0f} s in session B")
    ax.set_ylabel("airbag pressure (kPa)")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=TEAL, alpha=0.3, label="INHALE (PWM 255)"),
                       Patch(color=ORANGE, alpha=0.3, label="EXHALE (PWM 120, suction)")],
              loc="upper left", bbox_to_anchor=(0, 1.13), ncol=2, frameon=False)
    save(fig, "B_breathing_cycle_participantB.png")
    pk = np.array([c["peak_kPa"] for c in allc])
    mn = np.array([c["min_kPa"] for c in allc])
    cl = np.array([c["cycle_s"] for c in allc])
    ins = np.array([num(c["inhale_s"]) for c in allc])
    fc = [c for c in cycles(P, F, t0, t1 + 0.5)]
    md.append("\n## B. Breathing cycle (session 20261105_B)\n")
    md.append(f"Figure `B_breathing_cycle_participantB.png`: 60 s of P-line pressure from {t0:.0f} s (3rd inhale of the "
              f"longest pump-on period, {on:.0f}-{off:.0f} s). Shading from the F lines. All cycles: `B_cycles_participantB.csv`.\n")
    md.append(f"All complete cycles in B's {len(periods)} pump-on periods, n = {len(allc)} cycles "
              "(a cycle = INHALE start to the next INHALE start; the last, cut-off cycle of each period is left out):\n")
    md.append(f"- cycle length: mean {cl.mean():.2f} s (SD {cl.std(ddof=1):.2f}, range {cl.min():.2f}-{cl.max():.2f}) "
              f"→ {60 / cl.mean():.1f} breaths/min; inhale phase {np.nanmean(ins):.2f} s")
    md.append(f"- peak pressure per cycle: mean {pk.mean():.2f} kPa (SD {pk.std(ddof=1):.2f}, range {pk.min():.2f}-{pk.max():.2f}); "
              f"cycles reaching 3 kPa: {int((pk >= 2.95).sum())} of {len(pk)}")
    md.append(f"- minimum pressure per cycle: mean {mn.mean():.2f} kPa (SD {mn.std(ddof=1):.2f}, range {mn.min():.2f}-{mn.max():.2f}); "
              f"cycles reaching -1 kPa: {int((mn <= -0.95).sum())} of {len(mn)}")
    md.append(f"- the 60 s shown: n = {len(fc)} cycles, peak {np.mean([c['peak_kPa'] for c in fc]):.2f} kPa, "
              f"minimum {np.mean([c['min_kPa'] for c in fc]):.2f} kPa")
    return allc


# ---------------------------------------------------------------- C
def task_c(md):
    rows, would_rows = [], []
    for name in CLOSED_LOOP:
        folder = sdir(name)
        P, F, _ = esp32(folder)
        periods, would = pump_periods(folder)
        meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        kind = "control test" if "control" in meta["protocol_file"] else "demo"
        for i, (on, off, why, stop) in enumerate(periods):
            kpa = [x[1] for x in P if on <= x[0] <= (off if off == off else on + 1e9)]
            rows.append({"session": name.split("_", 1)[1], "type": kind, "order": meta["order"], "start": i + 1,
                         "start_s": round(on), "phase": phase_at(folder, on), "start_reason": why,
                         "on_s": round(off - on) if off == off else "", "stop_s": round(off) if off == off else "",
                         "stop_phase": phase_at(folder, off) if off == off else "", "stop_reason": stop,
                         "max_kPa": round(max(kpa), 2) if kpa else "", "inflated": "yes" if kpa and max(kpa) >= INFLATED_KPA else "no"})
        for t, why in would:
            would_rows.append({"session": name.split("_", 1)[1], "order": meta["order"], "would_on_s": round(t),
                               "phase": phase_at(folder, t), "reason": why})
    write_csv(OUT / "C_pump_decisions.csv", rows)
    write_csv(OUT / "C_would_on_control.csv", would_rows)
    md.append("\n## C. Pump decisions (4 closed-loop sessions)\n")
    md.append("Phases from the notes: `task (x)` = from `note:x` to `note:end`; `rest after x` = from that `note:end` "
              "to the next task note (or the end). Tables `C_pump_decisions.csv`, `C_would_on_control.csv`.\n")
    md.append("| session | type | # | start (s) | phase at start | start reason | on for (s) | stop reason | max kPa |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        md.append(f"| {r['session']} | {r['type']} | {r['start']} | {r['start_s']} | {r['phase']} | {r['start_reason']} | "
                  f"{r['on_s']} | {r['stop_reason']} | {r['max_kPa']} |")
    n_task = sum(r["phase"].startswith("task") for r in rows)
    n_rest = sum(r["phase"].startswith("rest") for r in rows)
    n_pre = len(rows) - n_task - n_rest
    md.append("")
    md.append(f"- Real pump starts: n = {len(rows)} over 4 sessions; during a task: {n_task}, during rest: {n_rest}"
              + (f", before the first task note: {n_pre}" if n_pre else "") + ".")
    for name in CLOSED_LOOP:
        sub = [r for r in rows if r["session"] == name.split("_", 1)[1]]
        md.append(f"  - {name}: {len(sub)} starts ({sum(r['phase'].startswith('task') for r in sub)} task, "
                  f"{sum(r['phase'].startswith('rest') for r in sub)} rest)")
    md.append(f"- Stop reasons: " + ", ".join(f"{k}: {sum(r['stop_reason'] == k for r in rows)}"
                                               for k in dict.fromkeys(r["stop_reason"] for r in rows)) + ".")
    md.append("\nControl blocks (rule ran, pump not allowed; logged as `would_on`):\n")
    md.append("| session | order | would start at (s) | phase | reason |")
    md.append("|---|---|---|---|---|")
    for r in would_rows:
        md.append(f"| {r['session']} | {r['order']} | {r['would_on_s']} | {r['phase']} | {r['reason']} |")
    return rows, would_rows


# ---------------------------------------------------------------- D
def task_d(md, c_rows, would_rows):
    PRE, POST = 30, 60
    events = []
    excluded = [r for r in c_rows if r["session"] == "D" and r["phase"].startswith("task (english)")]
    for r in c_rows:
        if r in excluded:
            continue
        events.append((f"20261105_{r['session']}", r["start_s"], "pump on" if r["inflated"] == "yes" else "pump cmd, bag flat"))
    for r in would_rows:
        events.append((f"20261105_{r['session']}", r["would_on_s"], "would start (control)"))
    cache, rows = {}, []
    lags = np.arange(-PRE, POST)
    traces = {}
    for name, t, group in events:
        if name not in cache:
            cache[name] = (eda_1hz(name), read_csv(sdir(name) / "features.csv"))
        (tt, v), feats = cache[name]
        idx = t + lags
        ok = (idx >= 0) & (idx < len(v))
        y = np.full(len(lags), np.nan)
        y[ok] = v[idx[ok]]
        pre = nanmean(y[(lags >= -10) & (lags < 0)])
        post = nanmean(y[(lags >= 10) & (lags < 30)])
        valid = nanmean([num(f["valid"]) for f in feats if t - PRE < num(f["t_ms"]) / 1000 <= t + POST])
        traces.setdefault(group, []).append(y - pre)
        rows.append({"session": name.split("_", 1)[1], "t_s": t, "group": group, "eda_-10_0_uS": round(pre, 3),
                     "eda_+10_+30_uS": round(post, 3), "change_uS": round(post - pre, 3),
                     "peak_rise_0_60_uS": round(np.nanmax(y[lags >= 0]) - pre, 3),
                     "valid_share_-30_+60": round(valid, 2)})
    write_csv(OUT / "D_eda_after_start.csv", rows)
    fig, ax = plt.subplots(figsize=SIZE)
    colors = {"pump on": TEAL, "would start (control)": ORANGE, "pump cmd, bag flat": NAVY}
    for g, ys in traces.items():
        for y in ys:
            ax.plot(lags, y, color=colors[g], lw=0.8, alpha=0.45, ls="--" if g == "pump cmd, bag flat" else "-")
        if len(ys) > 1:
            ax.plot(lags, np.nanmean(ys, axis=0), color=colors[g], lw=3, label=f"{g}: mean, n = {len(ys)}")
        else:
            ax.plot([], [], color=colors[g], lw=1, ls="--", label=f"{g}: n = 1 (A, period 1)")
    ax.axvline(0, color="#333333", lw=1)
    ax.axhline(0, color=GREY, lw=0.8)
    ax.axvspan(-10, 0, color=GREY, alpha=0.12, lw=0)
    ax.axvspan(10, 30, color=GREY, alpha=0.12, lw=0)
    ax.set_xlim(-PRE, POST - 1)
    ax.set_xlabel("seconds from pump start / would-start (t = 0)")
    ax.set_ylabel("EDA change (µS)\nvs mean of -10..0 s")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=11)
    save(fig, "D_eda_after_pump_start.png")
    md.append("\n## D. EDA around pump start (raw EDA, 1-s means of `eda.csv`)\n")
    md.append("Each event: EDA from -30 to +60 s, minus its own mean of -10..0 s (shaded windows: -10..0 s and +10..+30 s). "
              "Figure `D_eda_after_pump_start.png`, table `D_eda_after_start.csv`.\n")
    for r in excluded:
        md.append(f"- Excluded: D pump start at {r['start_s']} s (English = airbag block). The BITalino battery died; "
                  "EDA and ACC are invalid from about 878 s to the end (the `battery dead` note at 986 s was typed later). "
                  "D's English block is left out of this task and of any airbag-vs-control comparison; D's "
                  "math (control) would-start is kept (EDA before 878 s).\n")
    md.append("| session | t (s) | group | EDA -10..0 s (µS) | EDA +10..+30 s (µS) | change (µS) | largest rise 0-60 s (µS) | valid share |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        md.append(f"| {r['session']} | {r['t_s']} | {r['group']} | {r['eda_-10_0_uS']} | {r['eda_+10_+30_uS']} | "
                  f"{r['change_uS']} | {r['peak_rise_0_60_uS']} | {r['valid_share_-30_+60']} |")
    md.append("")
    for g in traces:
        ch = np.array([r["change_uS"] for r in rows if r["group"] == g])
        sess = sorted({r["session"] for r in rows if r["group"] == g})
        md.append(f"- {g}: n = {len(ch)} events from {len(sess)} session(s) ({', '.join(sess)}); change +10..+30 s minus -10..0 s: "
                  f"median {np.median(ch):+.3f} µS, mean {ch.mean():+.3f} µS, range {ch.min():+.3f} to {ch.max():+.3f}; "
                  f"{int((ch > 0).sum())} of {len(ch)} went up.")
    return rows


# ---------------------------------------------------------------- E
def task_e(md):
    folder = sdir("20261030_E")
    rows = read_csv(folder / "features_labeled.csv")
    steps = {}
    for r in rows:
        if r["phase_idx"] in ("", None):
            continue
        k = int(float(r["phase_idx"]))
        steps.setdefault(k, []).append(r)
    out = []
    for k in sorted(steps):
        rs = steps[k]
        valid = [r for r in rs if r["valid"] == "1"]
        out.append({"step": k, "condition": rs[0]["condition_name"], "after": rs[0]["after"],
                    "n_s": len(rs), "n_valid_s": len(valid),
                    "eda_uS": round(nanmean([num(r["eda_level"]) for r in rs]), 2),
                    "hr_bpm": round(nanmean([num(r["hr"]) for r in rs]), 1),
                    "arousal": round(nanmean([num(r["arousal"]) for r in valid]), 2) if valid else "",
                    "self_report": rs[0]["sr"],
                    "baseline_s": sum(r["is_baseline"] == "1" for r in rs)})
    write_csv(OUT / "E_steps_participantE.csv", out)
    labels = [o["condition"] if o["condition"] != "recovery" else "recov." for o in out]
    fig, axes = plt.subplots(1, 4, figsize=SIZE)
    for ax, key, lab, col in zip(axes, ["eda_uS", "hr_bpm", "arousal", "self_report"],
                                 ["EDA level (µS)", "heart rate (bpm)", "arousal (0-1)", "self-report (1-5)"],
                                 [TEAL, NAVY, ORANGE, GREY]):
        vals = [num(o[key]) for o in out]
        x = np.arange(len(out))
        ax.bar(x, vals, color=col, width=0.7)
        ax.set_xticks(x, [f"{o['step']} {l}" for o, l in zip(out, labels)], rotation=90)
        for xi, v in zip(x, vals):
            if not v == v:
                ax.text(xi, 0, "n/a", rotation=90, ha="center", va="bottom", fontsize=10, color="#555555")
        ax.set_ylabel(lab)
        if key == "hr_bpm":
            lo = np.nanmin(vals)
            ax.set_ylim(math.floor((lo - 8) / 5) * 5, None)
        if key in ("arousal",):
            ax.set_ylim(0, 1)
        if key == "self_report":
            ax.set_ylim(0, 5)
    fig.tight_layout(w_pad=1.5)
    save(fig, "E_steps_participantE.png")
    md.append("\n## E. Participant E, ML data collection (session 20261030_E, n = 1 person)\n")
    md.append("From `features_labeled.csv` (offline replay `replay_soft_smooth_gated_chat.csv`, baseline from the chat step, "
              "labels from `make_labels.py`). Means over all labelled seconds of a step (EDA level = 30 s mean, "
              "HR = median of valid beats over 30 s). Figure `E_steps_participantE.png`, table `E_steps_participantE.csv`.\n")
    md.append("| step | condition | after | seconds (valid) | EDA (µS) | HR (bpm) | arousal | self-report | baseline s |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for o in out:
        md.append(f"| {o['step']} | {o['condition']} | {o['after']} | {o['n_s']} ({o['n_valid_s']}) | {o['eda_uS']} | "
                  f"{o['hr_bpm']} | {o['arousal']} | {o['self_report']} | {o['baseline_s']} |")
    return out


def main():
    OUT.mkdir(exist_ok=True)
    md = ["# Presentation numbers (analysis_out)\n",
          "Generated by `analysis_out/make_figures.py` from `sessions/` (read only). Times are seconds from the start "
          "of the recording. Descriptive only: few sessions, no tests of an effect.\n"]
    task_a(md)
    task_b(md)
    c_rows, would_rows = task_c(md)
    task_d(md, c_rows, would_rows)
    task_e(md)
    (OUT / "results_auto.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("saved", OUT)


if __name__ == "__main__":
    main()
