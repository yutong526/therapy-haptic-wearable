"""The 1 Hz processing used live, in replay and in simulation: only the data source differs.

add(stream, t_ms, values) takes samples as the sources emit them (eda, beats, motion or acc);
step(t_ms) runs once per second: features -> confidence -> z -> p -> smoothed arousal ->
state machine -> baseline update, and returns one row (COLUMNS).

Phases: "warmup" (first WARMUP_S, ignored), "baseline" (rest period that sets the initial mu),
"run" (scoring). Output is valid only in "run".
"""

import collections
import math
import statistics

from . import settings as S
from .features import AccMotion, EdaFeatures, HeartRate, Motion

STEADY, RISE, SPIKE, RECOVERY = 0, 1, 2, 3
STATE_NAMES = ["steady", "rise", "spike", "recovery"]

COLUMNS = (["t_ms", "phase", *S.FEATURES, "eda_rise", "motion_rms", "c_eda", "c_motion", "c", "valid"]
           + [f"z_{k}" for k in S.FEATURES] + ["p", "arousal", "state"] + [f"mu_{k}" for k in S.FEATURES])


class Processor:
    def __init__(self, profile, warmup_s=S.WARMUP_S, baseline_s=S.BASELINE_S,
                 confidence_mode=S.CONFIDENCE_MODE, smoothing=S.SMOOTHING, baseline_mode=S.BASELINE_MODE,
                 motion_source="esp32"):
        assert confidence_mode in ("soft", "hard") and baseline_mode in ("fixed", "always", "gated")
        assert motion_source in ("esp32", "bitalino")
        self.profile = profile
        self.warmup_ms, self.baseline_ms = warmup_s * 1000, baseline_s * 1000
        self.min_valid_s = min(S.BASELINE_MIN_VALID_S, 0.75 * baseline_s)
        self.confidence_mode, self.smoothing, self.baseline_mode = confidence_mode, smoothing, baseline_mode
        self.eda, self.heart = EdaFeatures(), HeartRate()
        # esp32: MPU6050 summaries ("motion"); bitalino: ACC samples ("acc"). The other is ignored.
        self.motion_stream = "acc" if motion_source == "bitalino" else "motion"
        self.motion = AccMotion() if motion_source == "bitalino" else Motion()
        self.pending = collections.deque()  # (arrival, stream, t, values)
        self.phase = "warmup"
        self.collected = {k: [] for k in S.FEATURES}
        self.n_collected = 0
        self.mu = {k: float("nan") for k in S.FEATURES}
        self.mu_initial = None
        self.arousal = None
        self.state, self.state_since = STEADY, 0
        self.frozen_s = 0
        self.notices = []  # messages for the operator (baseline ready / extended)

    # ---- input ----
    def add(self, stream, t, values, arrival):
        """Queues a sample; step(now) uses only samples that arrived before now - ARRIVAL_MARGIN_MS.
        Live, a step can run a little late or in the middle of an EDA batch being added; the margin
        makes live and replay see exactly the same data."""
        self.pending.append((arrival, stream, t, values))

    def _apply(self, now):
        later = collections.deque()
        while self.pending:
            event = self.pending.popleft()
            if event[0] > now - S.ARRIVAL_MARGIN_MS:
                later.append(event)
            else:
                self._feed(*event[1:])
        self.pending = later

    def _feed(self, stream, t, values):
        if stream == "eda":
            self.eda.add(t, values[-1])
        elif stream == "beats":
            self.heart.add(t, values[1])
        elif stream == self.motion_stream:
            self.motion.add(t, values[-1] if stream == "acc" else values[2])

    # ---- 1 Hz step ----
    def step(self, now):
        self._apply(now)
        f = {"t_ms": now}
        f.update(self.eda.compute(now))
        f.update(self.heart.compute(now))
        f.update(self.motion.compute(now))
        c = f["c_eda"] * f["c_motion"]
        if self.confidence_mode == "hard":
            c = float(f["c_eda"] == 1 and f["still"])
        f["c"] = c
        valid = c >= S.VALID_C

        if self.phase == "warmup" and now >= self.warmup_ms:
            self.phase = "baseline"
        if self.phase == "baseline":
            self._collect_baseline(now, f, c)

        z = self._z(f)
        p = float("nan")
        if self.phase == "run":
            x = self.profile["bias"] + sum(self.profile["weights"][k] * z[k] for k in S.FEATURES)
            p = 1 / (1 + math.exp(-x))
            if self.arousal is None:
                if valid:
                    self.arousal = p
            elif not self.smoothing:
                if valid:
                    self.arousal = p
            else:
                self.arousal += S.ALPHA * c * (p - self.arousal)
            if valid and self.arousal is not None:
                self._update_state(now, f, z)
            self._update_baseline(f, c)
        else:
            valid = False

        f.update({"phase": self.phase, "valid": int(valid), "p": p,
                  "arousal": 0.0 if self.arousal is None else self.arousal, "state": self.state})
        f.update({f"z_{k}": z[k] for k in S.FEATURES})
        f.update({f"mu_{k}": self.mu[k] for k in S.FEATURES})
        return {k: f[k] for k in COLUMNS}

    def _collect_baseline(self, now, f, c):
        if c >= S.C_BASELINE:
            self.n_collected += 1
            for k in S.FEATURES:
                if not math.isnan(f[k]):
                    self.collected[k].append(f[k])
        if now - self.warmup_ms < self.baseline_ms:
            return
        if self.n_collected < self.min_valid_s:
            if (now - self.warmup_ms - self.baseline_ms) % 30000 == 0:
                self.notices.append(f"baseline extended: only {self.n_collected} s with c >= {S.C_BASELINE} "
                                    f"(need {self.min_valid_s:g}); keep still and relaxed")
            return
        for k, v in self.collected.items():
            if v:
                self.mu[k] = statistics.fmean(v)
        self.mu_initial = dict(self.mu)
        self.phase = "run"
        self.notices.append("baseline ready: " + ", ".join(f"{k}={v:.3g}" for k, v in self.mu.items()))

    def _z(self, f):
        z = {}
        for k in S.FEATURES:
            sigma = max(self.profile["sigma"][k], S.DEFAULT_SIGMA[k])
            if math.isnan(f[k]) or math.isnan(self.mu[k]):
                z[k] = 0.0  # missing feature (e.g. no heart rate) counts as baseline
            else:
                z[k] = max(-S.Z_CLIP, min(S.Z_CLIP, (f[k] - self.mu[k]) / sigma))
        return z

    def _update_state(self, now, f, z):
        if now - self.state_since < S.MIN_DWELL_S * 1000:
            return
        a = self.arousal
        if f["eda_rise"] >= S.SPIKE_RISE_US:
            state = SPIKE
        elif a >= S.RISE_AROUSAL or z["eda_slope"] >= S.RISE_SLOPE_Z:
            state = RISE
        elif self.state in (RISE, SPIKE):
            state = RECOVERY
        elif self.state == RECOVERY:
            state = STEADY if a < S.STEADY_AROUSAL and z["eda_slope"] <= 0 else RECOVERY
        else:
            state = STEADY
        if state != self.state:
            self.state, self.state_since = state, now

    def _update_baseline(self, f, c):
        beta = 0.0
        if self.baseline_mode == "always":
            beta = S.BASELINE_BETA if c >= S.C_BASELINE else 0.0
        elif self.baseline_mode == "gated":
            if self.state == STEADY and c >= S.C_BASELINE:
                beta, self.frozen_s = S.BASELINE_BETA, 0
            else:
                self.frozen_s += 1
                if self.frozen_s > S.BASELINE_FROZEN_S and c >= S.C_BASELINE:
                    beta = S.BASELINE_BETA * S.BASELINE_SLOW_FACTOR
        if beta == 0:
            return
        for k in S.FEATURES:
            if math.isnan(f[k]):
                continue
            self.mu[k] = f[k] if math.isnan(self.mu[k]) else (1 - beta) * self.mu[k] + beta * f[k]
