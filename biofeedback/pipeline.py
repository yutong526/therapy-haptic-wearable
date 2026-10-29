"""The 1 Hz processing used live, in replay and in simulation: only the data source differs.

add(stream, t_ms, values) takes samples as the sources emit them (eda, acc, beats) and the
markers; step(t_ms) runs once per second: features -> confidence -> z -> p -> smoothed arousal ->
state machine -> baseline update, and returns one row (COLUMNS).

Baseline (settings.py): with condition markers, mu comes from the BASELINE_SOURCE phase (e.g.
chat); until that phase is over, from BASELINE_PROVISIONAL (e.g. rest). Without any condition
marker, from the BASELINE_S after the warm-up. baseline_source in each row says which is in use.
Phases: "warmup" (no mu yet), "baseline" (collecting the first mu), "run" (scoring). Output is
valid only in "run".
"""

import collections
import math
import statistics

from . import settings as S
from .features import EdaFeatures, HeartRate, Motion

STEADY, RISE, SPIKE, STATE_RECOVERY = 0, 1, 2, 3
STATE_NAMES = ["steady", "rise", "spike", "state_recovery"]  # state_recovery: arousal falling (not the recovery condition)

COLUMNS = (["t_ms", "phase", "baseline_source", *S.FEATURES, "eda_rise", "motion_rms", "c_eda", "c_motion",
            "c", "valid"]
           + [f"z_{k}" for k in S.FEATURES] + ["p", "arousal", "state"] + [f"mu_{k}" for k in S.FEATURES])


class Processor:
    def __init__(self, profile, conditions, warmup_s=S.WARMUP_S, baseline_s=S.BASELINE_S,
                 confidence_mode=S.CONFIDENCE_MODE, smoothing=S.SMOOTHING, baseline_mode=S.BASELINE_MODE,
                 baseline_source=S.BASELINE_SOURCE):
        """conditions: {marker code: name}, from the protocol file."""
        assert confidence_mode in ("soft", "hard") and baseline_mode in ("fixed", "always", "gated")
        names = set(conditions.values())
        assert baseline_source in names, f"baseline source {baseline_source!r} is not a condition"
        self.profile, self.conditions = profile, conditions
        self.warmup_ms, self.baseline_ms = warmup_s * 1000, baseline_s * 1000
        self.min_valid_s = min(S.BASELINE_MIN_VALID_S, 0.75 * baseline_s)
        self.confidence_mode, self.smoothing, self.baseline_mode = confidence_mode, smoothing, baseline_mode
        # baseline sources by priority: a finished higher one replaces a lower one
        self.rank = {"none": 0, "time": 1}
        if S.BASELINE_PROVISIONAL in names and S.BASELINE_PROVISIONAL != baseline_source:
            self.rank[S.BASELINE_PROVISIONAL] = 2
        self.rank[baseline_source] = 3
        self.eda, self.heart, self.motion = EdaFeatures(), HeartRate(), Motion()
        self.pending = collections.deque()  # (arrival, stream, t, values)
        self.phase = "warmup"
        self.condition, self.condition_since = None, 0  # current condition name from the markers
        self.markers_seen = False
        self.collected = {}                 # source -> {"n": seconds, feature: [values]}
        self.n_collected = 0                # valid seconds of the source being collected now
        self.baseline_source = "none"
        self.mu = {k: float("nan") for k in S.FEATURES}
        self.mu_by_source = {}              # every baseline that was set, for baseline.json
        self.mu_initial = None              # mu of the source in use when it was set
        self.arousal = None
        self.state, self.state_since = STEADY, 0
        self.frozen_s = 0
        self.notices = []  # messages for the operator (baseline ready / not used)

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
        elif stream == "acc":
            self.motion.add(t, values[-1])
        elif stream == "markers":
            text = values[-1].strip()
            # a phase ends at its self-report (sr:N) or at the next code / end
            ends = text in self.conditions or text == "end" or text.lower().startswith("sr:")
            if ends:
                if self.condition is not None:
                    self._finish_phase(self.condition)
                self.condition = self.conditions.get(text)
                self.condition_since = t
            if text in self.conditions or text == "end":
                if not self.markers_seen:
                    self.markers_seen = True
                    self.collected.pop("time", None)  # markers in use: no time-based baseline

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

        f.update({"phase": self.phase, "baseline_source": self.baseline_source, "valid": int(valid), "p": p,
                  "arousal": 0.0 if self.arousal is None else self.arousal, "state": self.state})
        f.update({f"z_{k}": z[k] for k in S.FEATURES})
        f.update({f"mu_{k}": self.mu[k] for k in S.FEATURES})
        return {k: f[k] for k in COLUMNS}

    # ---- baseline ----
    def _wanted(self, source):
        return self.rank.get(source, 0) > self.rank[self.baseline_source] and source not in self.mu_by_source

    def _collect_baseline(self, now, f, c):
        if self.markers_seen:
            source = self.condition
            collecting = self._wanted(source) and now - self.condition_since >= S.BASELINE_SKIP_S * 1000
        else:
            source, collecting = "time", self._wanted("time") and now >= self.warmup_ms
        if collecting and c >= S.C_BASELINE:
            values = self.collected.setdefault(source, {"n": 0, **{k: [] for k in S.FEATURES}})
            values["n"] += 1
            for k in S.FEATURES:
                if not math.isnan(f[k]):
                    values[k].append(f[k])
        self.n_collected = self.collected.get(source, {}).get("n", 0) if collecting else 0
        if self.baseline_source == "none":
            self.phase = "baseline" if collecting else "warmup"
        if source == "time" and collecting:
            self._finish_time(now)

    def _finish_time(self, now):
        if now - self.warmup_ms < self.baseline_ms:
            return
        if self.n_collected < self.min_valid_s:
            if (now - self.warmup_ms - self.baseline_ms) % 30000 == 0:
                self.notices.append(f"baseline extended: only {self.n_collected} s with c >= {S.C_BASELINE} "
                                    f"(need {self.min_valid_s:g}); keep still and relaxed")
            return
        self._set_mu("time")

    def _finish_phase(self, source):
        """Called when the condition changes: use the phase that just ended if it is wanted."""
        values = self.collected.pop(source, None)
        if not self._wanted(source):
            return
        n = values["n"] if values else 0
        if n < S.BASELINE_PHASE_MIN_VALID_S:
            self.notices.append(f"baseline from {source} not used: only {n} s with c >= {S.C_BASELINE} "
                                f"(need {S.BASELINE_PHASE_MIN_VALID_S}); keeping {self.baseline_source}")
            return
        self._set_mu(source, values)

    def _set_mu(self, source, values=None):
        values = values or self.collected.pop(source)
        mu = {k: statistics.fmean(values[k]) if values[k] else float("nan") for k in S.FEATURES}
        self.mu, self.mu_initial = dict(mu), dict(mu)
        self.mu_by_source[source] = dict(mu)
        self.baseline_source, self.phase, self.frozen_s = source, "run", 0
        final = self.rank[source] == max(self.rank.values())
        self.notices.append(f"baseline ready ({source}{'' if final else ', provisional'}): "
                            + ", ".join(f"{k}={v:.3g}" for k, v in mu.items()))

    def baseline_status(self):
        """One line for the live window."""
        if self.n_collected:
            need = S.BASELINE_PHASE_MIN_VALID_S if self.markers_seen else self.min_valid_s
            source = self.condition if self.markers_seen else "time"
            return f"collecting {source}: {self.n_collected}/{need:g} s; in use: {self.baseline_source}"
        return f"in use: {self.baseline_source}"

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
            state = STATE_RECOVERY
        elif self.state == STATE_RECOVERY:
            state = STEADY if a < S.STEADY_AROUSAL and z["eda_slope"] <= 0 else STATE_RECOVERY
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
