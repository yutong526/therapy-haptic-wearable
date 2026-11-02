"""Per-person model: L2 logistic regression on the z features (docs/analysis_plan.md).

For each label map (A math, B game, C both, vs rest + chat): trains on the train halves of the
phases and tests on the test halves (baseline seconds excluded from the test), for the 30 s gap
(main) and the 60 s gap (check). Reports AUC (samples every 10 s and every 60 s), balanced accuracy,
a cross-task AUC (A on game, B on math), the weights, and whether the fallback rule would reject
the profile. Saves results/<subject>_train.csv and candidate profiles profiles/<subject>_map<X>.json
(not profiles/<subject>.json, which run_session.py loads; copy one there to use it).
With one session per person the numbers only show that the pipeline works, not how well it does.
Example:
    python tools/train_person.py results/dataset.csv --subject p01
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
Z = [f"z_{k}" for k in S.FEATURES]
EDA = [f"z_{k}" for k in S.FEATURES if k != "hr"]
CROSS = {"A": "game", "B": "math"}  # test-only task of each map


def fit(x, y, l2=S.TRAIN_L2, iterations=50):
    """Class-balanced L2 logistic regression (Newton / IRLS); returns (weights, bias)."""
    sw = np.where(y == 1, len(y) / (2 * y.sum()), len(y) / (2 * (len(y) - y.sum())))
    xb = np.column_stack([x, np.ones(len(x))])
    penalty = np.diag([l2] * x.shape[1] + [0.0])
    w = np.zeros(xb.shape[1])
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-xb @ w))
        grad = xb.T @ (sw * (y - p)) - penalty @ w
        hess = xb.T @ (xb * (sw * p * (1 - p))[:, None]) + penalty
        step = np.linalg.solve(hess, grad)
        w += step
        if np.abs(step).max() < 1e-8:
            break
    return w[:-1], w[-1]


def auc(score, y):
    """Probability that a random stress sample scores higher than a random calm one (ties count half)."""
    pos, neg = score[y == 1], score[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum())
                 / (len(pos) * len(neg)))


def balanced_accuracy(score, y):
    pred = score >= 0.5
    parts = [np.mean(pred[y == c] == c) for c in (0, 1) if (y == c).any()]
    return float(np.mean(parts)) if len(parts) == 2 else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", help="CSV from build_dataset.py")
    ap.add_argument("--subject", help="subject id (default: the only one in the dataset)")
    ap.add_argument("--maps", nargs="+", default=["A", "B", "C"])
    args = ap.parse_args()

    with open(args.dataset, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    subjects = sorted({r["subject"] for r in rows})
    subject = args.subject or (subjects[0] if len(subjects) == 1 else None)
    if subject not in subjects:
        sys.exit(f"give --subject, one of {subjects}")
    rows = [r for r in rows if r["subject"] == subject]
    x_all = np.array([[float(r[z]) for z in Z] for r in rows])
    t_all = np.array([int(r["t_ms"]) for r in rows])
    base = np.array([r["is_baseline"] == "1" for r in rows])

    results = []
    print(f"Subject {subject}: {len(rows)} samples. Features: {', '.join(Z)}")
    for m in args.maps:
        lab = np.array([r[f"label_{m}"] for r in rows])
        cond = np.array([r["condition"] for r in rows])
        for gap, col in ((S.CALIB_GAP_S, "split30"), (S.CALIB_GAP_CHECK_S, "split60")):
            sp = np.array([r[col] for r in rows])
            tr = (sp == "train") & np.isin(lab, ["0", "1"])
            te = (sp == "test") & np.isin(lab, ["0", "1"]) & ~base
            res = {"subject": subject, "map": m, "gap_s": gap, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                   "n_test_calm": int((te & (lab == "0")).sum()), "n_test_stress": int((te & (lab == "1")).sum())}
            ytr = (lab[tr] == "1").astype(float)
            if ytr.min() == ytr.max():
                print(f"  map {m}, gap {gap} s: train has one class only, skipped")
                continue
            w, b = fit(x_all[tr], ytr)
            score = 1 / (1 + np.exp(-(x_all @ w + b)))
            yte = (lab[te] == "1").astype(int)
            every60 = te & (t_all % 60000 == 0)
            res.update({"auc": auc(score[te], yte), "auc_60s": auc(score[every60], (lab[every60] == "1").astype(int)),
                        "bacc": balanced_accuracy(score[te], yte), "bias": b,
                        **{f"w_{z[2:]}": w[i] for i, z in enumerate(Z)}})
            if m in CROSS:  # trained task vs calm -> tested on the other task vs the same calm test samples
                ct = (sp == "test") & ~base & ((lab == "0") | (cond == CROSS[m]))
                res["auc_cross_" + CROSS[m]] = auc(score[ct], (cond[ct] == CROSS[m]).astype(int))
            eda_w = [w[Z.index(z)] for z in EDA]
            res["fallback"] = (res["bacc"] != res["bacc"] or res["bacc"] < S.FALLBACK_MIN_BACC
                               or min(eda_w) < S.FALLBACK_MIN_EDA_W)
            results.append(res)
            cross = f", cross-task AUC ({CROSS[m]}) {res['auc_cross_' + CROSS[m]]:.2f}" if m in CROSS else ""
            print(f"  map {m}, gap {gap:2d} s: train {res['n_train']}, test {res['n_test']} "
                  f"({res['n_test_calm']} calm / {res['n_test_stress']} stress) | AUC {res['auc']:.2f} "
                  f"(60 s samples {res['auc_60s']:.2f}), balanced acc {res['bacc']:.2f}{cross}")
            print("      weights " + ", ".join(f"{z[2:]} {w[i]:+.2f}" for i, z in enumerate(Z))
                  + f", bias {b:+.2f}" + ("  -> fallback (rule not met)" if res["fallback"] else ""))
            if gap == S.CALIB_GAP_S:
                profile = {"weights": {k: round(float(w[i]), 4) for i, k in enumerate(S.FEATURES)},
                           "bias": round(float(b), 4), "sigma": dict(S.DEFAULT_SIGMA), "lag_s": 0,
                           "mu_reference": {k: None for k in S.FEATURES},
                           "note": f"map {m}, trained on {res['n_train']} samples of {subject}; "
                                   f"test AUC {res['auc']:.2f}, balanced acc {res['bacc']:.2f}"
                                   + (", FAILS the fallback rule" if res["fallback"] else "")}
                path = ROOT / "profiles" / f"{subject}_map{m}.json"
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    out = ROOT / "results" / f"{subject}_train.csv"
    out.parent.mkdir(exist_ok=True)
    keys = sorted({k for r in results for k in r}, key=lambda k: list(results[0]).index(k) if k in results[0] else 99)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()} for r in results)
    print(f"Saved {out} and profiles/{subject}_map<X>.json (candidates; one session only: not a conclusion)")


if __name__ == "__main__":
    main()
