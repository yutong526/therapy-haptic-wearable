"""Record a session from the BITalino (EDA + ACC) and the RT7015 (PPG over BLE), output
state/arousal live (UDP, and serial to the ESP32 with --esp32).

Saves sessions/<subject>_<time>/: session.json, eda.csv, acc.csv, ppg.csv, beats.csv, esp32_log.csv,
markers.csv, features.csv (1 row per second) and baseline.json.
Type a marker + Enter while recording (or use the --plot window). Ctrl+C to stop. Examples:
    python tools/run_session.py --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot
    python tools/run_session.py --subject p02 --eda COM15 --plot --order game_first
    python tools/run_session.py --subject p01 --eda COM15 --esp32 COM11       # also send state to the ESP32
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
from biofeedback.live_window import LiveWindow  # noqa: E402
from biofeedback import ppg_ble  # noqa: E402
from biofeedback.esp32 import LOG_COLUMNS, Esp32Source, SerialTransport  # noqa: E402
from biofeedback.pipeline import COLUMNS, STATE_NAMES, Processor  # noqa: E402
from biofeedback.protocol import DEFAULT_PATH as PROTOCOL_PATH, default_order, load_protocol  # noqa: E402
from biofeedback.profile import load_profile  # noqa: E402
from biofeedback.publish import SerialCommandPublisher, UdpJsonPublisher  # noqa: E402
from biofeedback.recorder import Recorder  # noqa: E402
from biofeedback.sim import SimBitalino, SimPpgBle, SimTransport  # noqa: E402

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
    ap.add_argument("--baseline-source", default=S.BASELINE_SOURCE,
                    help=f"condition whose phase sets mu, e.g. chat or rest (default {S.BASELINE_SOURCE})")


def make_processor(args, profile, protocol):
    conditions = {code: c["name"] for code, c in protocol["conditions"].items()}
    return Processor(profile, conditions, warmup_s=args.warmup_s, baseline_s=args.baseline_s,
                     confidence_mode=args.confidence, smoothing=not args.no_smoothing,
                     baseline_mode=args.baseline_mode, baseline_source=args.baseline_source)


def fmt(v, spec):
    return "-" if isinstance(v, float) and math.isnan(v) else format(v, spec)


def status_line(row):
    return (f"{row['t_ms'] / 1000:6.0f} s | {row['phase']:8} | EDA {fmt(row['eda_level'], '.2f')} uS "
            f"| HR {fmt(row['hr'], '.0f')} | SCR/min {fmt(row['scr_rate'], '.1f')} "
            f"| c {row['c']:.2f} (eda {row['c_eda']:.0f} x motion {row['c_motion']:.2f}) "
            f"| {STATE_NAMES[row['state']]} {row['arousal']:.2f}{'' if row['valid'] else ' (invalid)'}")


def print_protocol(protocol, order, settling_s):
    sr = protocol["self_report"]
    print(f"Protocol, order {order}: settling {settling_s / 60:.3g} min, then")
    for i, step in enumerate(protocol["orders"][order], 1):
        name = protocol["conditions"][step["code"]]["name"]
        print(f"  {i}. type {step['code']}  {name:9} {step['seconds'] / 60:.3g} min  {step['note']}")
    print(f"  After each step the self-report sr:{sr['min']}..sr:{sr['max']}; at the end type end. "
          "Notes: note:<text>")


def marker_loop(add_marker):
    for line in sys.stdin:
        text = line.strip()
        if text:
            add_marker(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True, help="participant id, used for the folder and profile")
    ap.add_argument("--eda", help="BITalino serial port, e.g. COM13")
    ap.add_argument("--eda-channel", type=int, default=1, choices=range(1, 7), help="analog input (default A1)")
    ap.add_argument("--acc-channel", type=int, default=S.ACC_CHANNEL, choices=range(1, 7),
                    help=f"BITalino input of the ACC sensor, for the motion confidence (default A{S.ACC_CHANNEL})")
    ap.add_argument("--esp32", help="ESP32 serial port for the output link, e.g. COM11 (optional)")
    ap.add_argument("--ppg", choices=["ble", "none"], default="ble",
                    help="ble: RT7015 over its own BLE link (default); none: no heart rate")
    ap.add_argument("--ppg-address", default=S.PPG_BLE_ADDRESS,
                    help=f"RT7015 BLE address (default: scan for a name containing {S.PPG_BLE_NAME!r})")
    ap.add_argument("--simulate", action="store_true", help="simulated BITalino, RT7015 and ESP32, no hardware")
    add_processing_args(ap)
    ap.set_defaults(warmup_s=None)  # default: settling time of the protocol file
    ap.add_argument("--sinks", default="serial,udp", help="live outputs, comma separated: serial, udp (or none)")
    ap.add_argument("--udp", default=S.UDP_TARGET, help="UDP target host:port")
    ap.add_argument("--send-features", action="store_true", help="UDP messages also carry the full row")
    ap.add_argument("--out", default=str(ROOT / "sessions"), help="parent folder for sessions")
    ap.add_argument("--seconds", type=int, help="stop after this many seconds (default: until Ctrl+C)")
    ap.add_argument("--plot", action="store_true",
                    help="live window: plots, protocol timer, marker / self-report buttons, sensor lights")
    ap.add_argument("--protocol", default=str(PROTOCOL_PATH), help="protocol file (default config/protocol.yaml)")
    ap.add_argument("--order", help="order name from the protocol file (default: odd subject number -> "
                                    "first order, even -> second)")
    args = ap.parse_args()
    protocol = load_protocol(args.protocol)
    args.order = args.order or default_order(protocol, args.subject)
    if args.order not in protocol["orders"]:
        ap.error(f"--order must be one of {', '.join(protocol['orders'])}")
    if args.warmup_s is None:
        args.warmup_s = protocol["settling_s"]
    if not (args.simulate or args.eda or args.ppg == "ble"):
        ap.error("give --eda and/or --ppg ble, or --simulate")
    if args.acc_channel == args.eda_channel:
        ap.error("--acc-channel must differ from --eda-channel")

    profile_path = Path(args.profile) if args.profile else ROOT / "profiles" / f"{args.subject}.json"
    profile, found = load_profile(profile_path)
    print(f"Profile: {profile_path}" + ("" if found else " (not found, using default weights and sigma)"))

    clock = Clock()
    folder = Path(args.out) / f"{args.subject}_{clock.start_utc.astimezone():%Y%m%d_%H%M%S}"
    recorder = Recorder(folder, clock, {
        "subject": args.subject, "eda_port": args.eda, "eda_channel": args.eda_channel,
        "acc_channel": args.acc_channel, "motion_source": "bitalino",
        "ppg_source": args.ppg, "ppg_address": args.ppg_address,
        "esp32_port": args.esp32, "simulate": args.simulate,
        "profile": str(profile_path), "profile_found": found, "profile_values": profile,
        "warmup_s": args.warmup_s, "baseline_s": args.baseline_s, "confidence": args.confidence,
        "smoothing": not args.no_smoothing, "baseline_mode": args.baseline_mode,
        "baseline_source": args.baseline_source, "order": args.order,
        "protocol_file": args.protocol, "protocol": protocol,
        "settings": {k: v.hex(" ") if isinstance(v, bytes) else v for k, v in vars(S).items() if k.isupper()},
    })
    for name, columns in [("eda", EDA_COLUMNS), ("acc", ACC_COLUMNS), ("ppg", ppg_ble.PPG_COLUMNS),
                          ("beats", ppg_ble.BEAT_COLUMNS), ("esp32_log", LOG_COLUMNS),
                          ("markers", ["t_ms", "local_time", "text"]), ("features", COLUMNS)]:
        recorder.add_stream(name, columns)

    processor = make_processor(args, profile, protocol)
    plot = None
    if args.plot:
        lights = {"eda": S.EDA_RATE_HZ, "acc": S.EDA_RATE_HZ} if args.eda or args.simulate else {}
        if args.ppg == "ble":
            lights["ppg"] = S.PPG_DEFAULT_RATE
        plot = LiveWindow(clock, f"{folder.name} ({args.order})", protocol, args.order, args.warmup_s, lights,
                        lambda text: add_marker(text))
    marker_lock = threading.Lock()

    def add_marker(text):
        """From the terminal or the window."""
        with marker_lock:
            t = clock.ms()
            values = [datetime.now().astimezone().isoformat(), text]
            recorder.write("markers", t, values)
            with lock:
                processor.add("markers", t, values, t)
            if plot:
                plot.add_marker(t, text)
            print(f"[marker {t / 1000:.1f} s] {text}")
    lock = threading.Lock()  # sources add from their threads, the main loop steps

    def sink(stream, t, values):
        t = round(t, 1)  # same precision as the CSV, so a replay gives identical results
        recorder.write(stream, t, values)
        # BITalino: batch receive time; beats: sample where the beat was accepted
        arrival = round(values[0], 1) if stream in ("eda", "acc", "beats") else t
        with lock:
            processor.add(stream, t, values, arrival)
        if plot:
            plot.add(stream, t, values)

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
        esp32 = Esp32Source(transport, clock, recorder.write, stop)  # output link; its lines only logged
        sources.append(esp32)
    ppg_source = None
    if args.ppg == "ble":
        if args.simulate:
            ppg_source = SimPpgBle(clock, sink, stop)
        else:
            ppg_source = ppg_ble.PpgBleSource(args.ppg_address, clock, sink, stop)
        sources.append(ppg_source)
    for s in sources:
        s.start()
    threading.Thread(target=marker_loop, args=(add_marker,), daemon=True).start()

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

    print(f"EDA on A{args.eda_channel}, motion confidence from the ACC on A{args.acc_channel}")
    print(f"Baseline from the {args.baseline_source} phase (its code is entered at its start); "
          f"without condition codes: the {args.baseline_s} s after the {args.warmup_s} s warm-up.")
    print_protocol(protocol, args.order, args.warmup_s)
    print(f"Recording to {folder}\nType a marker + Enter at any time (in this terminal). Ctrl+C"
          + (" or End in the window" if plot else "") + " to stop.")

    def loop():
        tick = 1
        while args.seconds is None or tick <= args.seconds:
            if stop.wait(max(0, tick * 1000 - clock.ms()) / 1000):
                break
            now = tick * 1000
            with lock:
                row = processor.step(now)
                notices, processor.notices = processor.notices, []
            recorder.write("features", now, [row[c] for c in COLUMNS[1:]])
            if plot:
                plot.add_row(row, processor.baseline_status())
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
                    recorder.write_json("baseline.json", {"t_ms": now, "in_use": processor.baseline_source,
                                                          "by_source": processor.mu_by_source})
            if tick % STATUS_EVERY_S == 0:
                print(status_line(row))
            tick += 1
        stop.set()  # --seconds reached: also closes the plot window

    worker = threading.Thread(target=loop, name="loop") if plot else None
    try:
        if worker:
            worker.start()
            plot.show(stop)  # main thread until the window closes
        else:
            loop()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        if worker:
            worker.join()  # no feature row is written after the files are closed
        for s in sources:
            s.join(timeout=5)
        recorder.write_json("baseline.json", {"in_use": processor.baseline_source,
                                              "by_source": processor.mu_by_source, "mu_final": processor.mu})
        recorder.close()
        if ppg_source is not None:
            parser = ppg_source.stream.parser
            print(f"PPG: {parser.bad_frames} bad frames dropped, {parser.skipped_bytes} bytes skipped")
        print(f"Saved session to {folder}")


if __name__ == "__main__":
    main()
