"""Simulated BITalino, RT7015 and ESP32 output link for testing without hardware, paced in real time.

Scenario (seconds from start): calm, with a 60 s stress episode every 240 s starting at 300
(tonic EDA rises, SCRs and heart rate go up) and 4 s of arm movement every 100 s
(seen by the BITalino ACC on any input other than the EDA one, and as PPG artefacts).
The BLE PPG link drops for PPG_DROP_S at PPG_DROP_AT_S.
"""

import math
import random
import struct
import threading
import time

from . import settings as S
from .eda import EDA_VCC
from .ppg_ble import ADDR_RATE, ADDR_SAMPLES, PpgStream

STRESS_START_S, STRESS_EVERY_S, STRESS_LEN_S = 300, 240, 60
PPG_DROP_AT_S, PPG_DROP_S = 200, 6


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
    """ESP32 pump link: answers like firmware/pump (s / x / e / i / a / d / c / b, phase and pressure
    lines; breathing open loop, the bag pressure moving towards the stop pressures; no faults)."""

    def open(self):
        self.lines, self.phase, self.since = [], "IDLE", time.monotonic()
        self.kpa, self.exhale, self.inhale, self.buf = 0.0, -1.0, 3.0, ""
        self.pwm_in, self.pwm_out = 255, 120
        self.hold = None  # constant mode target (c<kPa>), None = breathing (b)

    def readline(self):
        time.sleep(0.2)
        now = time.monotonic()
        dt = now - self.since
        if self.phase == "FILL" and self.kpa >= self.hold - 0.2:
            self._change("HOLD")
        elif self.phase in ("INHALE", "EXHALE") and self.hold is not None:
            self._change("HOLD")
        elif self.phase == "HOLD" and self.hold is None:
            self._change("INHALE")
        elif self.phase == "INHALE" and dt >= 4:
            self._change("EXHALE")
        elif self.phase == "EXHALE" and dt >= 6:
            self._change("INHALE")
        elif self.phase == "DEFLATE" and self.kpa < 0.3:
            self._change("IDLE")
        target = {"FILL": self.hold, "HOLD": self.hold, "INHALE": self.inhale,
                  "EXHALE": self.exhale}.get(self.phase, 0.0)
        pwm_in = self.pwm_in if self.phase == "INHALE" and self.kpa < self.inhale else 0
        pwm_out = self.pwm_out if self.phase == "EXHALE" and self.kpa > self.exhale else 0
        rate = 0.1 if self.phase in ("FILL", "INHALE", "EXHALE") else 0.5
        self.kpa += (target - self.kpa) * rate + random.gauss(0, 0.02)
        if not self.lines:  # one line per read: no backlog
            self.lines.append(f"P,{int(now * 1000)},{self.kpa:.2f},{target:.2f},{pwm_in},{pwm_out},{self.phase}")
        return self.lines.pop(0)

    def _change(self, phase):
        self.phase, self.since = phase, time.monotonic()
        self.lines.append(f"F,{int(self.since * 1000)},{phase}")

    def write(self, data):
        for ch in data.decode(errors="replace"):
            if ch in "\r\n":
                if self.buf[:1] in ("e", "i") and len(self.buf) > 1:
                    setattr(self, "exhale" if self.buf[0] == "e" else "inhale", float(self.buf[1:]))
                elif self.buf[:1] in ("a", "d") and len(self.buf) > 1:
                    setattr(self, "pwm_in" if self.buf[0] == "a" else "pwm_out", int(float(self.buf[1:])))
                elif self.buf[:1] == "c" and len(self.buf) > 1:
                    self.hold = float(self.buf[1:])
                elif self.buf == "b":
                    self.hold = None
                self.buf = ""
            elif self.buf or ch not in "sxh":
                self.buf += ch
            elif ch == "s" and self.phase == "IDLE":
                self._change("INHALE" if self.hold is None else "FILL")
            elif ch == "x" and self.phase not in ("IDLE", "DEFLATE"):
                self._change("DEFLATE")

    def close(self):
        pass


def rt7015_frame(addr, payload):
    n = len(payload) + 2
    return bytes([0xA5, n, addr, n ^ addr]) + payload + bytes([0x5A])


class SimPpgBle(threading.Thread):
    """Stand-in for ppg_ble.PpgBleSource: RT7015 frames at 500 Hz, 20 samples per notification,
    board clock 0.05 % fast, 10-60 ms latency with occasional 200 ms stalls, now and then a frame
    split over two notifications or with a broken tail, and pulse-like SOUT edges during movement
    (small AOUT change, to be rejected). Feeds the same PpgStream as the real link."""

    RATE, PER_PACKET, DRIFT = 500, 20, 1.0005

    def __init__(self, clock, sink, stop):
        super().__init__(daemon=True, name="ppg_ble")
        self.clock, self.stop = clock, stop
        self.stream = PpgStream(sink)

    def run(self):
        t0 = self.clock.ms()
        self.stream.packet(t0, rt7015_frame(ADDR_RATE, bytes([S.PPG_RATES.index(self.RATE)])))
        print("[PPG] connected to simulated RT7015")
        n, onset, ibi, connected, carry = 0, 0.0, 0.8, True, b""
        while not self.stop.is_set():
            values = []
            for _ in range(self.PER_PACKET):
                t = n / self.RATE / self.DRIFT  # true seconds since start
                if t >= onset + ibi:
                    onset += ibi
                    bpm = 70 + 12 * stress(t) + 3 * math.sin(t / 20)
                    ibi = 60 / bpm * (1 + random.gauss(0, 0.03))
                phase = (t - onset) / ibi
                rising = phase < 0.15
                aout = 1200 + (900 * phase / 0.15 if rising else 900 * math.exp(-(phase - 0.15) * 5))
                sout = 3300 if rising else 0
                if 96 <= t % 100:  # movement: noise and spurious SOUT pulses
                    aout += random.gauss(0, 30)
                    if random.random() < 0.02:
                        sout = 3300 - sout
                values += [round(max(0, aout) * 10), sout * 10]
                n += 1
            t_last = (n - 1) / self.RATE / self.DRIFT
            latency = 200 if random.random() < 0.005 else random.uniform(10, 60)
            delay = (t0 + t_last * 1000 + latency - self.clock.ms()) / 1000
            if delay > 0:
                time.sleep(delay)
            if PPG_DROP_AT_S <= t_last < PPG_DROP_AT_S + PPG_DROP_S:
                if connected:
                    connected = False
                    self.stream.disconnected()
                    print("[PPG] simulated link drop")
                continue  # samples lost while disconnected
            if not connected:
                connected = True
                self.stream.packet(self.clock.ms(), rt7015_frame(ADDR_RATE, bytes([S.PPG_RATES.index(self.RATE)])))
                print("[PPG] simulated link back")
            data = carry + rt7015_frame(ADDR_SAMPLES, struct.pack(f"<{len(values)}H", *values))
            carry = b""
            r = random.random()
            if r < 0.003:
                data = data[:-1] + bytes([0])  # broken tail: frame dropped
            elif r < 0.01:
                data, carry = data[:30], data[30:]  # rest arrives with the next notification
            self.stream.packet(self.clock.ms(), data)
