"""Airbag test (config/airbag_test.yaml): effect of the airbag on EDA, heart rate and motion, per block.

For every block (condition code until sr:N / next code / end) of each session: airbag pressure
(mean / max kPa, from the firmware's P lines), EDA mean, EDA change
over the block (mean of the last 30 s minus the first 10 s), largest EDA rise within 10 s of the
block start (startle response to the inflation), SCRs per minute, heart rate, motion and the share
of valid seconds (c >= VALID_C; pump vibration can lower it). Then each airbag block minus the rest
block before it. Saves <session>/airbag_blocks.csv and airbag_overview.png; with several sessions
also results/airbag_summary.csv (one row per airbag block, with the site).
One run per body part, a few blocks each: the numbers describe these runs, not a tested effect.
Example:
    python tools/analyze_airbag.py sessions/20261103_Seona_wrist sessions/20261103_Seona_upper_arm
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from biofeedback import settings as S  # noqa: E402
from biofeedback.features import detect_scrs  # noqa: E402
from make_labels import phases_from, read_csv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ONSET_S = 10        # startle window after the block start
EDA_START_S = 10    # EDA change: mean of the last END_S minus the mean of the first START_S
EDA_END_S = 30
DIFF_KEYS = ["eda_mean_us", "eda_change_us", "rise_10s_us", "scr_per_min", "hr_bpm", "motion_rms_g", "valid_share"]


def nanmean(values):
    values = [v for v in values if v == v]
    return sum(values) / len(values) if values else float("nan")


def eda_10hz(rows):
    """eda.csv -> (t_ms, uS) at 10 Hz, smoothed as in the processing (bins of EDA_BIN_MS, EDA_SMOOTH_S)."""
    t = np.array([float(r["t_ms"]) for r in rows])
    us = np.array([float(r["eda_us"]) for r in rows])
    if not len(t):
        return t, us
    bins = (t // S.EDA_BIN_MS).astype(int)
    keys, idx = np.unique(bins, return_inverse=True)
    mean = np.bincount(idx, us) / np.bincount(idx)
    n = max(1, round(S.EDA_SMOOTH_S * 1000 / S.EDA_BIN_MS))
    padded = np.pad(mean, (n // 2, n - 1 - n // 2), mode="edge")  # no dip at the ends
    smooth = np.convolve(padded, np.ones(n) / n, mode="valid")
    return keys * S.EDA_BIN_MS + S.EDA_BIN_MS / 2, smooth


def block_stats(a, b, eda_t, eda_us, scrs, features):
    inside = (eda_t >= a) & (eda_t < b)
    x, tx = eda_us[inside], eda_t[inside]
    first = x[tx < a + EDA_START_S * 1000]
    last = x[tx >= b - EDA_END_S * 1000]
    onset = x[tx < a + ONSET_S * 1000]
    rows = [r for r in features if a <= float(r["t_ms"]) < b]
    minutes = (b - a) / 60000
    return {
        "eda_mean_us": float(x.mean()) if len(x) else float("nan"),
        "eda_change_us": float(last.mean() - first.mean()) if len(first) and len(last) else float("nan"),
        "rise_10s_us": float(onset.max() - onset[0]) if len(onset) else float("nan"),
        "scr_per_min": sum(a <= t < b for t, _ in scrs) / minutes if minutes else float("nan"),
        "hr_bpm": nanmean([float(r["hr"]) for r in rows]),
        "motion_rms_g": nanmean([float(r["motion_rms"]) for r in rows]),
        "valid_share": sum(float(r["c"]) >= S.VALID_C for r in rows) / len(rows) if rows else float("nan"),
    }


def pump_log(folder):
    """From esp32_log.csv (laptop t_ms): times the board started filling, and the pressure
    [(t_ms, kPa, target kPa)] from the P lines (pressure-control firmware)."""
    path = folder / "esp32_log.csv"
    if not path.exists():
        return [], []
    starts, pressure = [], []
    for r in read_csv(path):
        parts = r["line"].split(",")
        if parts[0] == "F" and parts[-1] in ("FILL", "INITIAL_INFLATE"):
            starts.append(float(r["t_ms"]))
        elif parts[0] == "P" and len(parts) == 7:
            try:
                pressure.append((float(r["t_ms"]), float(parts[2]), float(parts[3])))
            except ValueError:
                pass
    return starts, pressure


def analyze(folder):
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    conditions = {code: c["name"] for code, c in meta["protocol"]["conditions"].items()}
    pumped = {c["name"] for c in meta["protocol"]["conditions"].values() if c.get("pump")}
    markers = read_csv(folder / "markers.csv")
    features = read_csv(folder / "features.csv")
    eda_t, eda_us = eda_10hz(read_csv(folder / "eda.csv"))
    scrs = detect_scrs(eda_t, eda_us)
    end = float(features[-1]["t_ms"]) if features else 0.0
    starts, pressure = pump_log(folder)
    blocks, before = [], None
    for ph in phases_from(markers, conditions):
        a, b = ph["start"], ph["stop"] if ph["stop"] is not None else end
        if b - a < (EDA_START_S + EDA_END_S) * 1000:
            continue
        row = {"session": folder.name, "site": meta.get("site") or "", "block": ph["idx"], "condition": ph["name"],
               "start_s": round(a / 1000), "minutes": round((b - a) / 60000, 2), "sr": ph["sr"],
               "inflation_start_s": next((round((t - a) / 1000, 1) for t in starts if a <= t < b), "")}
        row.update(block_stats(a, b, eda_t, eda_us, scrs, features))
        inside = [kpa for t, kpa, _ in pressure if a <= t < b]
        row["kpa_mean"] = nanmean(inside)
        row["kpa_max"] = max(inside) if inside else float("nan")
        if ph["name"] in pumped and before is not None:
            for k in DIFF_KEYS:
                row[f"d_{k}"] = row[k] - before[k]
        if ph["name"] not in pumped:
            before = row
        blocks.append(row)
    return meta, blocks, (eda_t, eda_us, features, pressure, pumped)


def overview(folder, blocks, data):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eda_t, eda_us, features, pressure, pumped = data
    t_f = np.array([float(r["t_ms"]) for r in features]) / 60000
    panels = [("Airbag pressure (kPa)", np.array([p[0] for p in pressure]) / 60000,
               np.array([p[1] for p in pressure]))] if pressure else []
    panels += [("EDA (µS)", eda_t / 60000, eda_us),
              ("Heart rate (bpm)", t_f, np.array([float(r["hr"]) for r in features])),
              ("Motion, ACC 1 s SD (g)", t_f, np.array([float(r["motion_rms"]) for r in features])),
              ("Confidence c", t_f, np.array([float(r["c"]) for r in features]))]
    fig, axes = plt.subplots(len(panels), 1, sharex=True, figsize=(12, 2 * len(panels)))
    for ax, (title, t, y) in zip(axes, panels):
        ax.plot(t, y, color="#2a6fb0", lw=1.2)
        ax.set_title(title, fontsize=9, loc="left", pad=16 if ax is axes[0] else 6)
        ax.grid(axis="y", color="#e6e6e6", lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for blk in blocks:
            if blk["condition"] in pumped:
                a = blk["start_s"] / 60
                ax.axvspan(a, a + blk["minutes"], color="#9e9e9e", alpha=0.18, lw=0)
    for blk in blocks:
        axes[0].text(blk["start_s"] / 60, 1.0, " " + blk["condition"], transform=axes[0].get_xaxis_transform(),
                     va="bottom", fontsize=8, color="#555555")
    axes[-1].set_xlabel("minutes from session start (grey = airbag on)")
    fig.suptitle(f"{folder.name}", fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    out = folder / "airbag_overview.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def fmt(v, spec):
    return "-" if isinstance(v, float) and math.isnan(v) else format(v, spec) if isinstance(v, float) else str(v)


def write_csv(path, rows):
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()} for r in rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+", help="airbag test session folders")
    ap.add_argument("--out", default=str(ROOT / "results" / "airbag_summary.csv"))
    args = ap.parse_args()
    summary = []
    for s in args.sessions:
        folder = Path(s)
        meta, blocks, data = analyze(folder)
        print(f"\n{folder.name}  site: {meta.get('site') or '(not given)'}")
        print(f"  {'block':12} {'min':>4} {'EDA uS':>7} {'change':>7} {'rise10s':>7} {'SCR/min':>7} "
              f"{'HR':>5} {'motion g':>8} {'valid':>5} {'kPa mean/max':>12}  inflation")
        for b in blocks:
            print(f"  {b['block']:>2} {b['condition']:9} {b['minutes']:4.1f} {fmt(b['eda_mean_us'], '7.2f')} "
                  f"{fmt(b['eda_change_us'], '+7.2f')} {fmt(b['rise_10s_us'], '7.2f')} {fmt(b['scr_per_min'], '7.1f')} "
                  f"{fmt(b['hr_bpm'], '5.0f')} {fmt(b['motion_rms_g'], '8.3f')} {fmt(b['valid_share'], '5.0%')} "
                  f"{fmt(b['kpa_mean'], '5.1f'):>6}/{fmt(b['kpa_max'], '<5.1f')}  "
                  + (f"+{b['inflation_start_s']} s" if b["inflation_start_s"] != "" else ""))
        for b in blocks:
            if "d_eda_mean_us" in b:
                print(f"  airbag block {b['block']} minus the rest before: EDA {fmt(b['d_eda_mean_us'], '+.2f')} uS, "
                      f"SCR/min {fmt(b['d_scr_per_min'], '+.1f')}, HR {fmt(b['d_hr_bpm'], '+.0f')} bpm, "
                      f"motion {fmt(b['d_motion_rms_g'], '+.3f')} g, valid {fmt(b['d_valid_share'], '+.0%')}")
                summary.append(b)
        if blocks:
            write_csv(folder / "airbag_blocks.csv", blocks)
            print(f"  saved {folder / 'airbag_blocks.csv'} and {overview(folder, blocks, data)}")
        else:
            print("  no blocks (no condition markers?)")
    if len(args.sessions) > 1 and summary:
        out = Path(args.out)
        out.parent.mkdir(exist_ok=True)
        write_csv(out, summary)
        print(f"\nPer site (airbag minus the rest before, mean over its blocks):")
        sites = dict.fromkeys(b["site"] for b in summary)
        for site in sites:
            rows = [b for b in summary if b["site"] == site]
            print(f"  {site or '(no site)':12} {len(rows)} blocks | EDA {fmt(nanmean([r['d_eda_mean_us'] for r in rows]), '+.2f')} uS"
                  f" | SCR/min {fmt(nanmean([r['d_scr_per_min'] for r in rows]), '+.1f')}"
                  f" | HR {fmt(nanmean([r['d_hr_bpm'] for r in rows]), '+.0f')} bpm"
                  f" | valid {fmt(nanmean([r['d_valid_share'] for r in rows]), '+.0%')}")
        print(f"Saved {out}")


if __name__ == "__main__":
    main()
