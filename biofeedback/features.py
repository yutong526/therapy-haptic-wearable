"""Feature extractors for the three sensors. Each is fed samples and asked for its values at a time."""

import collections
import math
import statistics

import numpy as np

from . import settings as S


def _since(points, now, seconds):
    return [p for p in points if now - seconds * 1000 < p[0] <= now]


def _enough(points, seconds):
    return bool(points) and points[-1][0] - points[0][0] >= S.WINDOW_MIN_FRACTION * seconds * 1000


class ScrDetector:
    """Online SCR rule: a rise of at least SCR_MIN_AMP_US within SCR_MAX_RISE_S,
    counted when the signal has fallen back by half that amount."""

    def __init__(self):
        self.recent = collections.deque()
        self.trough = self.peak = None

    def reset(self):
        self.recent.clear()
        self.trough = self.peak = None

    def update(self, t, x):
        """Returns (t_peak, amplitude) when an SCR is confirmed, else None."""
        self.recent.append((t, x))
        while self.recent[0][0] < t - S.SCR_MAX_RISE_S * 1000:
            self.recent.popleft()
        if self.peak is None:
            low = min(self.recent, key=lambda p: p[1])
            if x - low[1] >= S.SCR_MIN_AMP_US:
                self.trough, self.peak = low, (t, x)
        elif x > self.peak[1]:
            self.peak = (t, x)
        elif self.peak[1] - x >= S.SCR_MIN_AMP_US / 2:
            scr = (self.peak[0], self.peak[1] - self.trough[1])
            self.reset()
            self.recent.append((t, x))
            return scr
        return None


def detect_scrs(t_ms, us):
    """Offline use of the same rule on a whole (smoothed, 10 Hz) recording: [(t_peak_ms, amplitude)]."""
    detector = ScrDetector()
    found = []
    for t, x in zip(t_ms, us):
        if not S.EDA_MIN_US <= x <= S.EDA_MAX_US:
            detector.reset()
            continue
        scr = detector.update(t, x)
        if scr:
            found.append(scr)
    return found


class EdaFeatures:
    """x1 level (uS), x2 slope (uS/min), x3 SCRs per minute; eda_rise over the short window;
    and the EDA signal checks (c_eda = 0 or 1). Input: 100 Hz samples in uS."""

    def __init__(self):
        self.raw = collections.deque(maxlen=S.EDA_RATE_HZ * 3)  # (t, uS) for the link check
        self.bins = collections.deque(maxlen=30)                 # (t, uS) 10 Hz, recent, unfiltered
        self.smooth = collections.deque()                        # (t, uS) 10 Hz, smoothed, in range only
        self.box = collections.deque(maxlen=max(1, round(S.EDA_SMOOTH_S * 1000 / S.EDA_BIN_MS)))
        self.scrs = collections.deque()                          # (t_peak, amplitude)
        self.scr = ScrDetector()
        self.bin = None  # [index, sum, n]
        self.longest = max(S.EDA_LEVEL_WINDOW_S, S.EDA_SLOPE_WINDOW_S, S.SHORT_WINDOW_S) * 1000

    def add(self, t, us):
        self.raw.append((t, us))
        index = int(t // S.EDA_BIN_MS)
        if self.bin is not None and index > self.bin[0]:
            self._add_bin((self.bin[0] + 1) * S.EDA_BIN_MS, self.bin[1] / self.bin[2])
            self.bin = None
        if self.bin is None:
            self.bin = [index, 0.0, 0]
        self.bin[1] += us
        self.bin[2] += 1

    def _add_bin(self, t, x):
        # A bin is timed at its end, so it is only used once all of its samples can have arrived.
        self.bins.append((t, x))
        if not S.EDA_MIN_US <= x <= S.EDA_MAX_US:  # out of range: gap in the features
            self.box.clear()
            self.scr.reset()
            return
        self.box.append(x)
        y = statistics.fmean(self.box)
        self.smooth.append((t, y))
        while self.smooth[0][0] < t - self.longest:
            self.smooth.popleft()
        scr = self.scr.update(t, y)
        if scr:
            self.scrs.append(scr)
        while self.scrs and self.scrs[0][0] < t - S.SCR_WINDOW_S * 1000:
            self.scrs.popleft()

    def compute(self, now):
        nan = float("nan")
        last = _since(self.raw, now, 1)
        bins = _since(self.bins, now, 1)
        link_ok = len(last) >= S.EDA_MIN_FILL * S.EDA_RATE_HZ
        range_ok = all(S.EDA_MIN_US <= x <= S.EDA_MAX_US for _, x in bins)
        rate_ok = all(abs(b[1] - a[1]) * 1000 / (b[0] - a[0]) <= S.EDA_MAX_RATE_US_PER_S
                      for a, b in zip(bins, bins[1:]) if b[0] > a[0])
        f = {"eda_level": nan, "eda_slope": nan, "scr_rate": nan, "eda_rise": nan,
             "c_eda": float(link_ok and range_ok and rate_ok)}

        level = _since(self.smooth, now, S.EDA_LEVEL_WINDOW_S)
        if _enough(level, S.EDA_LEVEL_WINDOW_S):
            f["eda_level"] = statistics.fmean(x for _, x in level)
        slope = _since(self.smooth, now, S.EDA_SLOPE_WINDOW_S)
        if _enough(slope, S.EDA_SLOPE_WINDOW_S):
            t = np.array([p[0] for p in slope]) / 60000  # minutes
            f["eda_slope"] = float(np.polyfit(t - t[0], [p[1] for p in slope], 1)[0])
        scr_span = _since(self.smooth, now, S.SCR_WINDOW_S)
        if _enough(scr_span, S.SCR_WINDOW_S):
            minutes = (scr_span[-1][0] - scr_span[0][0]) / 60000
            f["scr_rate"] = len(_since(self.scrs, now, S.SCR_WINDOW_S)) / minutes
        short = [x for _, x in _since(self.smooth, now, S.SHORT_WINDOW_S)]
        if short:
            f["eda_rise"] = short[-1] - min(short)
        return f


class HeartRate:
    """x4: median heart rate of the valid beats in HR_WINDOW_S. Input: IBIs (ms) of the detected beats."""

    def __init__(self):
        self.history = collections.deque(maxlen=S.IBI_HISTORY)  # recent in-range IBIs
        self.valid = collections.deque()                         # (t, ibi_ms)

    def add(self, t, ibi):
        """Returns True if the beat is valid."""
        if not S.IBI_MIN_MS <= ibi <= S.IBI_MAX_MS:
            return False
        ok = False
        if len(self.history) >= S.IBI_MIN_HISTORY:
            med = statistics.median(self.history)
            ok = abs(ibi - med) <= S.IBI_MAX_DEV * med
        self.history.append(ibi)
        if ok:
            self.valid.append((t, ibi))
        while self.valid and self.valid[0][0] < t - S.HR_WINDOW_S * 1000:
            self.valid.popleft()
        return ok

    def compute(self, now):
        beats = _since(self.valid, now, S.HR_WINDOW_S)
        if not beats or now - beats[-1][0] > S.HR_MAX_GAP_S * 1000:
            return {"hr": float("nan")}
        return {"hr": 60000 / statistics.median(ibi for _, ibi in beats)}


class Motion:
    """Motion confidence from the BITalino ACC (one axis, 100 Hz): standard deviation over the last
    second of samples received. Insensitive to gravity, orientation and calibration offset.
    No samples for LINK_TIMEOUT_S (BITalino link dropped) -> c_motion = 0."""

    def __init__(self):
        self.last = None  # (t, std)
        self.samples = collections.deque()  # (t, g)

    def add(self, t, g):
        self.samples.append((t, g))
        while self.samples[0][0] <= t - 1000:
            self.samples.popleft()
        rms = float("nan")
        if len(self.samples) >= S.EDA_MIN_FILL * S.EDA_RATE_HZ:
            rms = statistics.pstdev(g for _, g in self.samples)
        self.last = (t, rms)

    def compute(self, now):
        if self.last is None or now - self.last[0] > S.LINK_TIMEOUT_S * 1000 or math.isnan(self.last[1]):
            return {"motion_rms": float("nan"), "c_motion": 0.0, "still": False}  # no link / too few samples
        rms = self.last[1]
        c = (S.MOTION_HI_G - rms) / (S.MOTION_HI_G - S.MOTION_LO_G)
        return {"motion_rms": rms, "c_motion": min(1.0, max(0.0, c)), "still": rms < S.MOTION_HARD_G}
