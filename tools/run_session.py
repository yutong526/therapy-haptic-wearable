"""Record a session from the BITalino (EDA) and the ESP32 (PPG + IMU) and output state/arousal live.

Saves sessions/<subject>_<time>/: session.json, eda.csv, acc.csv (with --acc-channel), ppg.csv,
imu.csv (record mode only), beats.csv, esp32_log.csv, markers.csv, features.csv (1 row per second) and baseline.json.
Type any text + Enter while recording to add a marker (e.g. "baseline", "math start", "score 3").
Press Ctrl+C to stop. Examples:
    python tools/run_session.py --subject p01 --eda COM13 --esp32 COM5
    python tools/run_session.py --subject p01 --eda COM15 --acc-channel 5 --esp32 COM11   # motion from BITalino ACC
    python tools/run_session.py --subject demo --eda COM13 --esp32 COM5 --esp32-mode live --warmup-s 30 --baseline-s 60
    python tools/run_session.py --subject test --simulate --warmup-s 20 --baseline-s 40
"""

import argparse
import math
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402
from biofeedback.clock import Clock  # noqa: E402
from biofeedback.eda import ACC_COLUMNS, EDA_COLUMNS, EdaSource  # noqa: E402
from biofeedback.esp32 import BEAT_COLUMNS, IMU_COLUMNS, LOG_COLUMNS, PPG_COLUMNS, Esp32Source, SerialTransport  # noqa: E402
from biofeedback.pipeline import COLUMNS, STATE_NAMES, Processor  # noqa: E402
from biofeedback.profile import load_profile  # noqa: E402
from biofeedback.publish import SerialCommandPublisher, UdpJsonPublisher  # noqa: E402
from biofeedback.recorder import Recorder  # noqa: E402
from biofeedback.sim import SimBitalino, SimTransport  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATUS_EVERY_S = 5


def add_processing_args(ap):
    """Options shared with replay_session.py."""
    ap.add_argument("--profile", help="profile JSON (default profiles/<subject>.json; missing -> defaults)")
    ap.add_argument("--warmup-s", type=int, default=S.WARMUP_S, help=f"ignored start (default {S.WARMUP_S})")
    ap.add_argument("--baseline-s", type=int, default=S.BASELINE_S,
                    help=f"rest period after the warm-up that sets mu (default {S.BASELINE_S})")
    ap.add_argument("--confidence", choices=["soft", "hard"], default=S.CONFIDENCE_MODE)
    ap.add_argument("--no-smoothing", action="store_true", help="arousal = p, no confidence-weighted smoothing")
    ap.add_argument("--baseline-mode", choices=["fixed", "always", "gated"], default=S.BASELINE_MODE)


def make_processor(args, profile, motion_source):
    return Processor(profile, warmup_s=args.warmup_s, baseline_s=args.baseline_s,
                     confidence_mode=args.confidence, smoothing=not args.no_smoothing,
                     baseline_mode=args.baseline_mode, motion_source=motion_source)


def fmt(v, spec):
    return "-" if isinstance(v, float) and math.isnan(v) else format(v, spec)


def status_line(row):
    return (f"{row['t_ms'] / 1000:6.0f} s | {row['phase']:8} | EDA {fmt(row['eda_level'], '.2f')} uS "
            f"| HR {fmt(row['hr'], '.0f')} | SCR/min {fmt(row['scr_rate'], '.1f')} "
            f"| c {row['c']:.2f} (eda {row['c_eda']:.0f} x motion {row['c_motion']:.2f}) "
            f"| {STATE_NAMES[row['state']]} {row['arousal']:.2f}{'' if row['valid'] else ' (invalid)'}")


def marker_loop(clock, recorder):
    for line in sys.stdin:
        text = line.strip()
        if text:
            t = clock.ms()
            recorder.write("markers", t, [datetime.now().astimezone().isoformat(), text])
            print(f"[marker {t / 1000:.1f} s] {text}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True, help="participant id, used for the folder and profile")
    ap.add_argument("--eda", help="BITalino serial port, e.g. COM13")
    ap.add_argument("--eda-channel", type=int, default=1, choices=range(1, 7), help="analog input (default A1)")
    ap.add_argument("--acc-channel", type=int, choices=range(1, 7),
                    help="BITalino input of the ACC sensor; motion confidence then comes from it, not the ESP32 IMU")
    ap.add_argument("--esp32", help="ESP32 serial port, e.g. COM5")
    ap.add_argument("--esp32-mode", choices=["record", "live"], default="record",
                    help="record: also 100 Hz raw PPG/IMU (USB only); live: only what the processing needs")
    ap.add_argument("--simulate", action="store_true", help="simulated BITalino and ESP32, no hardware")
    add_processing_args(ap)
    ap.add_argument("--sinks", default="serial,udp", help="live outputs, comma separated: serial, udp (or none)")
    ap.add_argument("--udp", default=S.UDP_TARGET, help="UDP target host:port")
    ap.add_argument("--send-features", action="store_true", help="UDP messages also carry the full row")
    ap.add_argument("--out", default=str(ROOT / "sessions"), help="parent folder for sessions")
    ap.add_argument("--seconds", type=int, help="stop after this many seconds (default: until Ctrl+C)")
    args = ap.parse_args()
    if not (args.simulate or args.eda or args.esp32):
        ap.error("give --eda and/or --esp32, or --simulate")
    if args.acc_channel and not (args.eda or args.simulate):
        ap.error("--acc-channel needs --eda (the ACC is read by the BITalino)")
    if args.acc_channel == args.eda_channel:
        ap.error("--acc-channel must differ from --eda-channel")
    motion_source = "bitalino" if args.acc_channel else "esp32"

    profile_path = Path(args.profile) if args.profile else ROOT / "profiles" / f"{args.subject}.json"
    profile, found = load_profile(profile_path)
    print(f"Profile: {profile_path}" + ("" if found else " (not found, using default weights and sigma)"))

    clock = Clock()
    folder = Path(args.out) / f"{args.subject}_{clock.start_utc.astimezone():%Y%m%d_%H%M%S}"
    recorder = Recorder(folder, clock, {
        "subject": args.subject, "eda_port": args.eda, "eda_channel": args.eda_channel,
        "acc_channel": args.acc_channel, "motion_source": motion_source,
        "esp32_port": args.esp32, "esp32_mode": args.esp32_mode, "simulate": args.simulate,
        "profile": str(profile_path), "profile_found": found, "profile_values": profile,
        "warmup_s": args.warmup_s, "baseline_s": args.baseline_s, "confidence": args.confidence,
        "smoothing": not args.no_smoothing, "baseline_mode": args.baseline_mode,
        "settings": {k: v for k, v in vars(S).items() if k.isupper()},
    })
    streams = [("eda", EDA_COLUMNS)] + ([("acc", ACC_COLUMNS)] if args.acc_channel else [])
    for name, columns in streams + [("ppg", PPG_COLUMNS), ("imu", IMU_COLUMNS),
                          ("beats", BEAT_COLUMNS), ("esp32_log", LOG_COLUMNS),
                          ("markers", ["t_ms", "local_time", "text"]), ("features", COLUMNS)]:
        recorder.add_stream(name, columns)

    processor = make_processor(args, profile, motion_source)
    lock = threading.Lock()  # sources add from their threads, the main loop steps

    def sink(stream, t, values):
        t = round(t, 1)  # same precision as the CSV, so a replay gives identical results
        recorder.write(stream, t, values)
        arrival = round(values[0], 1) if stream in ("eda", "acc") else t  # BITalino: batch receive time
        with lock:
            processor.add(stream, t, values, arrival)

    stop = threading.Event()
    sources = []
    esp32 = None
    if args.simulate or args.eda:
        if args.simulate:
            def factory(port, timeout):
                return SimBitalino(port, timeout, eda_channel=args.eda_channel)
        else:
            from bitalino import BITalino as factory
        sources.append(EdaSource(args.eda or "sim", args.eda_channel, S.EDA_RATE_HZ, clock, sink, stop, factory,
                                 acc_channel=args.acc_channel))
    if args.simulate or args.esp32:
        transport = SimTransport() if args.simulate else SerialTransport(args.esp32)
        esp32 = Esp32Source(transport, clock, sink, stop, mode=args.esp32_mode)
        sources.append(esp32)
    for s in sources:
        s.start()
    threading.Thread(target=marker_loop, args=(clock, recorder), daemon=True).start()

    publishers = []
    for name in filter(None, (s.strip() for s in args.sinks.split(","))):
        if name == "serial" and esp32:
            publishers.append(SerialCommandPublisher(esp32))
            print("Sending C,state,arousal,valid,t_ms to the ESP32")
        elif name == "udp":
            host, _, port = args.udp.rpartition(":")
            publishers.append(UdpJsonPublisher(host or "127.0.0.1", int(port)))
            print(f"Sending state/arousal as UDP JSON to {args.udp}")
        elif name != "none" and name != "serial":
            ap.error(f"unknown sink {name}")

    print(f"Motion confidence from {'BITalino ACC A%d' % args.acc_channel if args.acc_channel else 'ESP32 IMU'}")
    print(f"Warm-up {args.warmup_s} s, then {args.baseline_s} s rest for the baseline: keep still and relaxed.")
    print(f"Recording to {folder}\nType a marker + Enter at any time. Ctrl+C to stop.")
    tick = 1
    try:
        while args.seconds is None or tick <= args.seconds:
            stop.wait(max(0, tick * 1000 - clock.ms()) / 1000)
            now = tick * 1000
            with lock:
                row = processor.step(now)
                notices, processor.notices = processor.notices, []
            recorder.write("features", now, [row[c] for c in COLUMNS[1:]])
            message = {"t_ms": now, "state": row["state"], "arousal": round(row["arousal"], 4),
                       "valid": bool(row["valid"])}
            if args.send_features:
                message["features"] = row
            for p in publishers:
                p.publish(message)
            recorder.flush()
            for n in notices:
                print(f"[{now / 1000:.0f} s] {n}")
                if n.startswith("baseline ready"):
                    recorder.write_json("baseline.json", {"t_ms": now, "mu_initial": processor.mu_initial})
            if tick % STATUS_EVERY_S == 0:
                print(status_line(row))
            tick += 1
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        for s in sources:
            s.join(timeout=5)
        recorder.write_json("baseline.json", {"mu_initial": processor.mu_initial, "mu_final": processor.mu})
        recorder.close()
        print(f"Saved session to {folder}")


if __name__ == "__main__":
    main()
