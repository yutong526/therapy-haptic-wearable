"""Feed a recorded session through the same Processor as the live loop.

Samples are fed in the order the laptop received them (EDA by recv_ms, so a batch becomes
available at the same moment as live) and step() runs at every whole second.
"""

import csv
import json
import sys
import time
from pathlib import Path

from . import settings as S
from .eda import acquired, adc_to_g, adc_to_microsiemens, channel_bits
from .ppg_ble import detect_beats


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_events(session):
    """[(arrival_ms, stream, t_ms, values)] sorted by arrival."""
    session = Path(session)
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    eda_channel, acc_channel = meta.get("eda_channel") or 1, meta.get("acc_channel")
    channels = acquired([eda_channel, acc_channel])
    bits = channel_bits(eda_channel, channels)
    if meta.get("motion_source") != "bitalino" or meta.get("ppg_source") != "ble":
        print("Warning: recorded before motion came from the BITalino ACC and PPG from BLE only; "
              "motion confidence and/or heart rate will be missing", file=sys.stderr)
    events = []
    for r in read_csv(session / "eda.csv"):
        adc = int(r["eda_raw_adc"])  # uS recomputed from the ADC: the CSV value is rounded
        values = [round(float(r["recv_ms"]), 1), int(r["sample_index"]), int(r["sequence"]),
                  int(r["sequence_gap_mod16"]), adc, adc_to_microsiemens(adc, bits)]
        events.append((values[0], "eda", float(r["t_ms"]), values))
    if acc_channel and (session / "acc.csv").exists():
        # g recomputed with the calibration the session was recorded with
        rec = meta.get("settings", {})
        cmin, cmax = rec.get("ACC_CMIN", S.ACC_CMIN), rec.get("ACC_CMAX", S.ACC_CMAX)
        acc_bits = channel_bits(acc_channel, channels)
        for r in read_csv(session / "acc.csv"):
            adc = int(r["acc_raw_adc"])
            values = [round(float(r["recv_ms"]), 1), int(r["sample_index"]), adc,
                      adc_to_g(adc, acc_bits, cmin, cmax)]
            events.append((values[0], "acc", float(r["t_ms"]), values))
    if meta.get("ppg_source") == "ble":
        # beats detected again from the raw PPG (current thresholds), arriving at their confirm sample
        samples = [(float(r["t_ms"]), float(r["aout_mv"]), float(r["sout_mv"])) for r in read_csv(session / "ppg.csv")]
        for t, values in detect_beats(samples):
            events.append((values[0], "beats", t, values))
    if (session / "markers.csv").exists():
        for r in read_csv(session / "markers.csv"):
            t = float(r["t_ms"])
            events.append((t, "markers", t, [r["local_time"], r["text"]]))
    events.sort(key=lambda e: e[0])
    return events


def run(processor, events, speed="fast", on_row=None):
    """Returns the per-second rows. speed: "fast" or "real" (paced like the recording)."""
    rows = []
    if not events:
        return rows
    end = events[-1][0]
    start = time.monotonic()
    i, tick = 0, 1
    while tick * 1000 <= end:
        now = tick * 1000
        while i < len(events) and events[i][0] <= now:
            arrival, stream, t, values = events[i]
            processor.add(stream, t, values, arrival)
            i += 1
        if speed == "real":
            delay = start + tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        row = processor.step(now)
        rows.append(row)
        if on_row:
            on_row(row)
        tick += 1
    return rows
