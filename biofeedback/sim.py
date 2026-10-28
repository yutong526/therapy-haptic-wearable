"""Simulated BITalino and ESP32 for testing without hardware, paced in real time.

Scenario (seconds from start): calm, with a 60 s stress episode every 240 s starting at 300
(tonic EDA rises, SCRs and heart rate go up) and 4 s of arm movement every 100 s
(seen by the ESP32 IMU and by the BITalino ACC on any input other than the EDA one).
"""

import math
import random
import time

from . import settings as S
from .eda import EDA_VCC

STRESS_START_S, STRESS_EVERY_S, STRESS_LEN_S = 300, 240, 60


def stress(t):
    """0..1 stress level at t seconds (ramps up and down over 15 s)."""
    if t < STRESS_START_S:
        return 0.0
    into = (t - STRESS_START_S) % STRESS_EVERY_S
    return max(0.0, min(1.0, into / 15, (STRESS_LEN_S + 15 - into) / 15))


class SimBitalino:
    """Stand-in for bitalino.BITalino(port, timeout)."""

    def __init__(self, port, timeout, eda_channel=1):
        self.eda_channel = eda_channel
        self.n = 0
        self.next_scr = random.uniform(10, 20)
        self.scrs = []
        self.tonic = 3.0

    def start(self, rate, channels):
        self.rate, self.t0 = rate, time.monotonic()
        self.channels = sorted(set(channels))  # 0-based, column order of bitalino.read

    def read(self, n):
        rows = []
        for _ in range(n):
            t = self.n / self.rate
            s = stress(t)
            if t >= self.next_scr:
                self.scrs.append(t)
                self.next_scr = t + (random.uniform(5, 10) if s > 0.5 else random.uniform(20, 40))
            self.tonic += (3.0 + 0.8 * s - self.tonic) / (20 * self.rate)  # follows stress over ~20 s
            us = self.tonic
            for onset in self.scrs[-5:]:  # rise ~1.5 s, decay ~4 s
                dt = t - onset
                if dt > 0:
                    us += 0.4 * (1 - math.exp(-dt / 0.7)) * math.exp(-dt / 4)
            adc = round(us * 0.132 / EDA_VCC * 1024 + random.gauss(0, 1))
            g = 1 + random.gauss(0, 0.3 if 96 <= t % 100 else 0.005)
            acc = round(S.ACC_CMIN + (g + 1) / 2 * (S.ACC_CMAX - S.ACC_CMIN))
            rows.append([self.n % 16, 0, 0, 0, 0]
                        + [adc if c == self.eda_channel - 1 else acc for c in self.channels])
            self.n += 1
        delay = self.t0 + self.n / self.rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return rows

    def stop(self):
        pass

    def close(self):
        pass


class SimTransport:
    """Produces firmware lines: R at 100 Hz (record mode only), B per beat, S every second."""

    def open(self):
        self.t0 = time.monotonic()
        self.n = 0
        self.next_beat = 1.0
        self.pending = []
        self.raw = False
        self.motion_sq = []

    def readline(self):
        if self.pending:
            return self.pending.pop(0)
        t = self.n / 100
        delay = self.t0 + t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.n += 1
        ms = int(t * 1000)
        bpm = 70 + 12 * stress(t) + 3 * math.sin(t / 20)
        moving = 96 <= t % 100
        shake = 0.3 if moving else 0.005
        acc = [random.gauss(0, shake), random.gauss(0, shake), 1 + random.gauss(0, shake)]
        gyro = [random.gauss(0, 80 if moving else 0.5) for _ in range(3)]
        mag = math.sqrt(sum(a * a for a in acc))
        self.motion_sq.append((mag - 1) ** 2)
        lines = []
        if t >= self.next_beat:
            ibi = 60000 / bpm * (1 + random.gauss(0, 0.03))
            if moving:
                ibi *= random.uniform(0.5, 1.6)  # motion artefacts
            self.next_beat += ibi / 1000
            lines.append(f"B,{ms},{round(ibi)},{round(60000 / ibi)}")
        if self.n % 100 == 0:
            rms = math.sqrt(sum(self.motion_sq) / len(self.motion_sq))
            lines.append(f"S,{ms},{mag:.4f},{rms:.4f},{abs(gyro[0]):.3f}")
            self.motion_sq = []
        if self.raw:
            ppg = 1650 + 300 * math.sin(2 * math.pi * bpm / 60 * t)
            lines.append(f"R,{ms},{ppg:.0f},{3000 if ppg > 1800 else 100},"
                         + ",".join(f"{a:.4f}" for a in acc) + "," + ",".join(f"{g:.3f}" for g in gyro))
        if not lines:
            return ""
        self.pending = lines[1:]
        return lines[0]

    def write(self, data):
        if data in (b"r", b"t"):
            self.raw = data == b"r"

    def close(self):
        pass
