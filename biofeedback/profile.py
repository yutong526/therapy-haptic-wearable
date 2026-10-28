"""Per-person profile: 4 weights, bias, 4 sigmas, lag (14 numbers with the 4 reference means).

{"weights": {...}, "bias": b, "sigma": {...}, "lag_s": d, "mu_reference": {...}}
mu_reference is only for reference: mu is measured at the start of every session.
Missing values fall back to the defaults in settings.py.
"""

import copy
import json
from pathlib import Path

from . import settings as S

DEFAULT_PROFILE = {
    "weights": dict(S.DEFAULT_WEIGHTS),
    "bias": S.DEFAULT_BIAS,
    "sigma": dict(S.DEFAULT_SIGMA),
    "lag_s": S.DEFAULT_LAG_S,
    "mu_reference": {k: None for k in S.FEATURES},
}


def load_profile(path):
    """Returns (profile, found)."""
    profile = copy.deepcopy(DEFAULT_PROFILE)
    path = Path(path) if path else None
    if path is None or not path.exists():
        return profile, False
    data = json.loads(path.read_text(encoding="utf-8"))
    for group in ("weights", "sigma", "mu_reference"):
        for k in S.FEATURES:
            if data.get(group, {}).get(k) is not None:
                profile[group][k] = float(data[group][k])
    for key in ("bias", "lag_s"):
        if data.get(key) is not None:
            profile[key] = float(data[key])
    return profile, True


def save_profile(path, profile):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
