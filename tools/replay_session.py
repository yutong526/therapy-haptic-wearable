"""Re-run the live processing on a recorded session and plot an overview.

Uses the same Processor as run_session.py, so switches can be compared offline (E3: --confidence,
E4: --baseline-mode). Writes <session>/replay_<tag>.csv (same columns as features.csv) and
replay_<tag>.png. Examples:
    python tools/replay_session.py sessions/p01_20261028_140000
    python tools/replay_session.py sessions/p01_20261028_140000 --confidence hard --baseline-mode fixed
    python tools/replay_session.py sessions/p01_20261028_140000 --speed real --print
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import replay  # noqa: E402
from biofeedback.pipeline import COLUMNS, STATE_NAMES  # noqa: E402
from biofeedback.profile import load_profile  # noqa: E402
from run_session import add_processing_args, make_processor, status_line  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def plot(session, rows, markers, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = [r["t_ms"] / 1000 for r in rows]
    eda = replay.read_csv(session / "eda.csv")
    eda_t = [float(r["t_ms"]) / 1000 for r in eda[::10]]
    eda_y = [float(r["eda_us"]) for r in eda[::10]]
    panels = [
        ("EDA (uS)", [(eda_t, eda_y, "EDA 10 Hz"), (t, [r["mu_eda_level"] for r in rows], "mu level")]),
        ("Heart rate (bpm)", [(t, [r["hr"] for r in rows], "hr (valid beats)"),
                              (t, [r["mu_hr"] for r in rows], "mu hr")]),
        ("Score", [(t, [r["p"] for r in rows], "p"), (t, [r["arousal"] for r in rows], "arousal")]),
        ("State", [(t, [r["state"] for r in rows], "state")]),
        ("Confidence", [(t, [r["c"] for r in rows], "c"), (t, [r["c_motion"] for r in rows], "c_motion"),
                        (t, [r["c_eda"] for r in rows], "c_eda")]),
    ]
    fig, axes = plt.subplots(len(panels), 1, sharex=True, figsize=(13, 2 * len(panels) + 1))
    for ax, (title, series) in zip(axes, panels):
        for x, y, label in series:
            style = {"drawstyle": "steps-post"} if title in ("State", "Confidence") else {}
            ax.plot(x, y, lw=1, label=label, **style)
        ax.set_title(title, fontsize=9, loc="left")
        ax.legend(loc="upper left", bbox_to_anchor=(1.005, 1.0), fontsize=8)
        ax.grid(alpha=0.3)
        for m in markers:
            ax.axvline(float(m["t_ms"]) / 1000, color="grey", ls="--", lw=0.8)
    axes[3].set_yticks(range(4), STATE_NAMES)
    for m in markers:
        axes[0].text(float(m["t_ms"]) / 1000, 1.02, m["text"], transform=axes[0].get_xaxis_transform(),
                     fontsize=8, rotation=30, va="bottom")
    run_start = next((r["t_ms"] / 1000 for r in rows if r["phase"] == "run"), None)
    for ax in axes:
        if run_start is not None:
            ax.axvspan(0, run_start, color="grey", alpha=0.1)  # warm-up + baseline
    axes[-1].set_xlabel("time (s)")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="session folder")
    add_processing_args(ap)
    ap.add_argument("--speed", choices=["fast", "real"], default="fast")
    ap.add_argument("--print", action="store_true", help="print a status line every 5 s")
    ap.add_argument("--tag", help="output name suffix (default from the switches)")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    session = Path(args.session)
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    # Options not given on the command line default to what the session was recorded with
    for key in ("warmup_s", "baseline_s", "confidence", "baseline_mode"):
        if f"--{key.replace('_', '-')}" not in sys.argv and key in meta:
            setattr(args, key, meta[key])
    if "--no-smoothing" not in sys.argv and "smoothing" in meta:
        args.no_smoothing = not meta["smoothing"]
    profile_path = Path(args.profile) if args.profile else ROOT / "profiles" / f"{meta.get('subject')}.json"
    profile, found = load_profile(profile_path)
    print(f"Profile: {profile_path}" + ("" if found else " (not found, using defaults)"))

    processor = make_processor(args, profile, meta.get("motion_source", "esp32"))

    def on_row(row):
        for n in processor.notices:
            print(f"[{row['t_ms'] / 1000:.0f} s] {n}")
        processor.notices.clear()
        if args.print and row["t_ms"] % 5000 == 0:
            print(status_line(row))

    rows = replay.run(processor, replay.load_events(session), args.speed, on_row)
    tag = args.tag or f"{args.confidence}_{'smooth' if not args.no_smoothing else 'raw'}_{args.baseline_mode}"
    out = session / f"replay_{tag}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([round(r[c], 4) if isinstance(r[c], float) else r[c] for c in COLUMNS])
    run_rows = [r for r in rows if r["phase"] == "run"]
    valid = sum(r["valid"] for r in run_rows)
    print(f"{len(rows)} s replayed, {len(run_rows)} s scored, {valid} s valid; "
          + ", ".join(f"{name} {sum(r['state'] == i for r in run_rows)} s" for i, name in enumerate(STATE_NAMES)))
    print(f"Saved {out}")
    if not args.no_plot:
        markers = replay.read_csv(session / "markers.csv") if (session / "markers.csv").exists() else []
        png = out.with_suffix(".png")
        plot(session, rows, markers, png)
        print(f"Saved {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
