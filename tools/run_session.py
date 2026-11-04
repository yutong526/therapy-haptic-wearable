"""Record a session from the BITalino (EDA + ACC) and the RT7015 (PPG over BLE), output
state/arousal live (UDP, and serial to the ESP32 with --esp32), drive the airbag pump (--esp32).

Saves sessions/<date>_<name>[_<site>]/: session.json, eda.csv, acc.csv, ppg.csv, beats.csv,
esp32_log.csv, pump.csv, markers.csv, features.csv (1 row per second) and baseline.json.
Pump (--pump): "protocol" = on during conditions marked pump: true in the protocol file (airbag
test), "auto" = closed loop from the state (settings PUMP_*), "off".
Type a marker + Enter while recording (or use the --plot window). Ctrl+C to stop. Examples:
    python tools/run_session.py --name Seona --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot
    python tools/run_session.py --subject p02 --eda COM15 --plot --order game_first
    python tools/run_session.py --name Seona --site wrist --protocol config/airbag_test.yaml --esp32 COM11 \
        --eda COM15 --baseline-source rest --sinks none --plot                 # airbag test, one body part
    python tools/run_session.py --subject p01 --eda COM15 --esp32 COM11 --pump auto --plot   # closed loop
    python tools/run_session.py --subject test --simulate --warmup-s 20 --baseline-s 40
    python tools/run_session.py --resume sessions/p01_20261030_161150 --plot   # continue a stopped session
"""

import argparse
import json
import math
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402
from biofeedback.clock import Clock  # noqa: E402
from biofeedback.eda import ACC_COLUMNS, EDA_COLUMNS, EdaSource  # noqa: E402
from biofeedback.live_window import LiveWindow, Progress  # noqa: E402
from biofeedback import ppg_ble, replay  # noqa: E402
from biofeedback.esp32 import LOG_COLUMNS, Esp32Source, SerialTransport  # noqa: E402
from biofeedback.pipeline import COLUMNS, STATE_NAMES, Processor  # noqa: E402
from biofeedback.protocol import DEFAULT_PATH as PROTOCOL_PATH, USE_PATH, default_order, load_protocol  # noqa: E402
from biofeedback.profile import load_profile  # noqa: E402
from biofeedback.publish import SerialCommandPublisher, UdpJsonPublisher  # noqa: E402
from biofeedback.pump import PumpController  # noqa: E402
from biofeedback.recorder import Recorder  # noqa: E402
from biofeedback.sim import SimBitalino, SimPpgBle, SimTransport  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATUS_EVERY_S = 5
PUMP_COLUMNS = ["t_ms", "on", "mode", "reason"]  # pump.csv: commanded changes (board: esp32_log F lines)
PUMP_LABELS = {"off": "PUMP OFF", "hold": "INFLATE (constant pressure)", "breathe": "BREATHING ASSIST"}


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
    if args.baseline_source not in conditions.values() and S.BASELINE_PROVISIONAL in conditions.values():
        # e.g. config/airbag_test.yaml has no chat phase
        print(f"Note: no '{args.baseline_source}' condition in the protocol, baseline from "
              f"'{S.BASELINE_PROVISIONAL}'")
        args.baseline_source = S.BASELINE_PROVISIONAL
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
        print(f"  {i}. {'' if protocol['auto_steps'] else 'type '}{step['code']}  {name:9} "
              f"{step['seconds'] / 60:.3g} min  {step['note']}")
    if sr:
        print(f"  After each step the self-report sr:{sr['min']}..sr:{sr['max']}; at the end type end. "
              "Notes: note:<text>")


def safe_name(name):
    """Folder-safe version of a name (letters, digits, - and _ kept; spaces become _)."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip()) or "unnamed"


def marker_loop(add_marker):
    for line in sys.stdin:
        text = line.strip()
        if text:
            add_marker(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="participant name: the session folder is <date>_<name>")
    ap.add_argument("--subject", help="anonymous participant id (e.g. p01) for the analysis, the profile and the "
                                      "counterbalanced order (default: the name)")
    ap.add_argument("--resume", metavar="FOLDER",
                    help="continue a stopped session in its folder: same settings (ports can be given again), "
                         "time axis continued, state restored from the recorded data")
    ap.add_argument("--eda", help="BITalino serial port, e.g. COM13")
    ap.add_argument("--eda-channel", type=int, default=1, choices=range(1, 7), help="analog input (default A1)")
    ap.add_argument("--acc-channel", type=int, default=S.ACC_CHANNEL, choices=range(1, 7),
                    help=f"BITalino input of the ACC sensor, for the motion confidence (default A{S.ACC_CHANNEL})")
    ap.add_argument("--esp32", help="ESP32 (airbag pump) serial port, e.g. COM11 (optional)")
    ap.add_argument("--pump", choices=["protocol", "auto", "off"], default="protocol",
                    help="protocol: pump on during pump: true conditions (default); auto: closed loop; off")
    ap.add_argument("--site", help="body part of the airbag (airbag test), saved and added to the folder name")
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
    ap.add_argument("--protocol", help="protocol file (default config/protocol.yaml with --pump protocol, "
                                       "else config/therapy.yaml)")
    ap.add_argument("--manual-steps", action="store_true",
                    help="enter the step codes by hand even if the protocol file has auto_steps: true")
    ap.add_argument("--order", help="order name from the protocol file (default: odd subject number -> "
                                    "first order, even -> second)")
    args = ap.parse_args()
    meta = None
    if args.resume:
        meta = json.loads((Path(args.resume) / "session.json").read_text(encoding="utf-8"))
        given = {a.split("=")[0] for a in sys.argv[1:] if a.startswith("--")}
        for key, arg in [("name", "name"), ("subject", "subject"), ("eda_port", "eda"), ("eda_channel", "eda_channel"),
                         ("acc_channel", "acc_channel"), ("ppg_source", "ppg"), ("ppg_address", "ppg_address"),
                         ("esp32_port", "esp32"), ("pump_mode", "pump"), ("site", "site"),
                         ("simulate", "simulate"), ("warmup_s", "warmup_s"),
                         ("baseline_s", "baseline_s"), ("confidence", "confidence"),
                         ("baseline_mode", "baseline_mode"), ("baseline_source", "baseline_source"),
                         ("order", "order")]:
            if f"--{arg.replace('_', '-')}" not in given and meta.get(key) is not None:
                setattr(args, arg, meta[key])
        if "--no-smoothing" not in given and "smoothing" in meta:
            args.no_smoothing = not meta["smoothing"]
    elif not (args.name or args.subject):
        ap.error("give --name (and --subject), or --resume FOLDER")
    args.subject = args.subject or args.name
    args.name = args.name or args.subject
    args.protocol = args.protocol or str(PROTOCOL_PATH if args.pump == "protocol" else USE_PATH)
    protocol = meta["protocol"] if meta and meta.get("protocol") else load_protocol(args.protocol)
    protocol.setdefault("auto_steps", False)  # sessions recorded before this key
    data_collection = bool(protocol["self_report"])  # otherwise demo / test: no ML hints
    if data_collection and not args.order and not any(ch.isdigit() for ch in args.subject):
        print(f"Note: subject {args.subject!r} has no number, so the order is not counterbalanced "
              "automatically; give --subject pNN or --order")
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
    if data_collection or found:
        print(f"Profile: {profile_path}" + ("" if found else " (not found, using default weights and sigma)"))

    processor = make_processor(args, profile, protocol)
    old_markers, old_end_ms, first_tick = [], 0, 1
    if args.resume:
        folder = Path(args.resume)
        print(f"Restoring the state from the recorded data in {folder} ...")
        rows = replay.run(processor, replay.load_events(folder))
        processor.notices.clear()
        old_markers = replay.read_csv(folder / "markers.csv")
        old_end_ms = max([rows[-1]["t_ms"] if rows else 0] + [float(m["t_ms"]) for m in old_markers])
        clock = Clock(datetime.fromisoformat(meta["start_utc"]))
        first_tick = int(clock.ms() // 1000) + 1
        print(f"  {old_end_ms / 1000:.0f} s restored, baseline in use: {processor.baseline_source}; "
              f"stopped for {(clock.ms() - old_end_ms) / 1000:.0f} s (recorded as a pause)")
    else:
        clock = Clock()
        base = Path(args.out) / (f"{clock.start_utc.astimezone():%Y%m%d}_{safe_name(args.name)}"
                                 + (f"_{safe_name(args.site)}" if args.site else ""))
        folder, n = base, 1
        while folder.exists():  # same person twice on one day
            n += 1
            folder = base.with_name(f"{base.name}_{n}")
    recorder = Recorder(folder, clock, resume=bool(args.resume), meta={
        "name": args.name, "subject": args.subject, "eda_port": args.eda, "eda_channel": args.eda_channel,
        "acc_channel": args.acc_channel, "motion_source": "bitalino",
        "ppg_source": args.ppg, "ppg_address": args.ppg_address,
        "esp32_port": args.esp32, "pump_mode": args.pump, "site": args.site, "simulate": args.simulate,
        "profile": str(profile_path), "profile_found": found, "profile_values": profile,
        "warmup_s": args.warmup_s, "baseline_s": args.baseline_s, "confidence": args.confidence,
        "smoothing": not args.no_smoothing, "baseline_mode": args.baseline_mode,
        "baseline_source": args.baseline_source, "order": args.order,
        "protocol_file": args.protocol, "protocol": protocol,
        "settings": {k: v.hex(" ") if isinstance(v, bytes) else v for k, v in vars(S).items() if k.isupper()},
    })
    for name, columns in [("eda", EDA_COLUMNS), ("acc", ACC_COLUMNS), ("ppg", ppg_ble.PPG_COLUMNS),
                          ("beats", ppg_ble.BEAT_COLUMNS), ("esp32_log", LOG_COLUMNS), ("pump", PUMP_COLUMNS),
                          ("markers", ["t_ms", "local_time", "text"]), ("features", COLUMNS)]:
        recorder.add_stream(name, columns)

    plot = None
    if args.plot:
        lights = {"eda": S.EDA_RATE_HZ, "acc": S.EDA_RATE_HZ} if args.eda or args.simulate else {}
        if args.ppg == "ble":
            lights["ppg"] = S.PPG_DEFAULT_RATE
        plot = LiveWindow(clock, f"{folder.name} ({args.order})", protocol, args.order, args.warmup_s, lights,
                          lambda text: add_marker(text), pump_mode=args.pump if args.simulate or args.esp32 else None)
    marker_lock = threading.Lock()
    esp32 = None
    # state: "off", "hold" (constant pressure) or "breathe" (breathing assist); manual: None = protocol /
    # controller decide (they use "breathe"), else the state forced by pump:inflate / breathe / off
    pump = {"state": "off", "code_t": None, "manual": None, "wanted": "off"}
    drive = {"mode": args.pump}  # who decides when not by hand; pump:auto turns "off" into "auto"
    # auto_steps: follows the markers like the window does, the loop enters the next code when time is up
    steps = Progress(protocol, args.order, args.warmup_s) if protocol["auto_steps"] and not args.manual_steps else None

    mode_lines = {"hold": f"c{S.PUMP_HOLD_KPA:.2f}\n".encode(), "breathe": b"b\n"}  # firmware line commands

    def apply_pump(state, reason, t=None):
        if state == pump["state"]:
            return
        pump["state"] = state
        t = clock.ms() if t is None else t
        recorder.write("pump", t, [int(state != "off"), state, reason])
        if esp32:
            esp32.set_pump(state != "off", mode_lines.get(state, b""))
        if plot:
            plot.set_pump(state, reason)
            if drive["mode"] == "auto" and pump["manual"] is None:
                plot.add_marker(t, "pump:" + ("on" if state != "off" else "off"))
        print(f"*** [pump {t / 1000:.1f} s] {PUMP_LABELS[state]} ({reason}) ***")

    def set_pump(on, reason, t=None):
        """Protocol / controller (on: breathing assist); ignored while the pump is forced by hand."""
        pump["wanted"] = "breathe" if on else "off"
        if pump["manual"] is None:
            apply_pump(pump["wanted"], reason, t)

    def manual_pump(text, t):
        """pump:inflate (or pump:on) / pump:breathe / pump:off force the pump in any --pump mode,
        pump:auto hands it back."""
        cmd = text.split(":", 1)[1].strip().lower()
        state = {"inflate": "hold", "on": "hold", "breathe": "breathe", "off": "off"}.get(cmd)
        if state:
            pump["manual"] = state
            apply_pump(state, f"manual {cmd}", t)
        elif cmd == "auto":
            pump["manual"] = None
            if drive["mode"] == "off":  # started with --pump off: the closed loop takes over
                drive["mode"] = "auto"
                pump["wanted"] = "breathe" if controller.on else "off"
            reason = "auto: closed loop decides" if drive["mode"] == "auto" else "auto: protocol decides"
            if pump["wanted"] == pump["state"]:  # nothing to switch: still say so
                print(f"*** [pump {t / 1000:.1f} s] {reason}, {PUMP_LABELS[pump['state']]} ***")
                if plot:
                    plot.set_pump(pump["state"], reason)
            else:
                apply_pump(pump["wanted"], reason, t)
        else:
            print("Pump commands: pump:inflate, pump:breathe, pump:off, pump:auto")

    def pump_from_marker(text, t):
        """--pump protocol: on from a pump: true code until sr:N, the next code, pause or end."""
        cond = protocol["conditions"].get(text)
        if cond is not None:
            pump["code_t"] = t
        elif text.lower().startswith("sr:"):
            if pump["code_t"] is not None and t - pump["code_t"] <= S.SR_LATE_S * 1000:
                return  # late self-report of the step before, as in the processing
        elif text not in ("pause", "end"):
            return
        set_pump(bool(cond and cond.get("pump")), f"marker {text}", t)

    def add_marker(text, t=None):
        """From the terminal or the window (t: only when resuming)."""
        with marker_lock:
            t = clock.ms() if t is None else t
            values = [(clock.start_utc + timedelta(milliseconds=t)).astimezone().isoformat(), text]
            recorder.write("markers", t, values)
            with lock:
                processor.add("markers", t, values, t)
            if plot:
                plot.add_marker(t, text)
            print(f"[marker {t / 1000:.1f} s] {text}")
            if steps:
                steps.marker(t / 1000, text)
            if text.lower().startswith("pump:"):
                manual_pump(text, t)
            elif args.pump == "protocol":
                pump_from_marker(text, t)
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
        targets = (f"e{S.PUMP_EXHALE_KPA:.2f}\ni{S.PUMP_INHALE_KPA:.2f}\na{S.PUMP_INHALE_PWM}\n"
                   f"d{S.PUMP_EXHALE_PWM}\nb\n").encode()  # breathing mode
        def esp32_sink(stream, t, values):  # board lines: logged, and pressure / phase in the window
            recorder.write(stream, t, values)
            if plot:
                plot.add(stream, t, values)
        esp32 = Esp32Source(transport, clock, esp32_sink, stop, setup=targets)  # pump link
        sources.append(esp32)
    elif args.pump != "off" and (args.pump == "auto" or any(c.get("pump") for c in protocol["conditions"].values())):
        print("Note: no --esp32, so the pump commands are only written to pump.csv")
    controller = PumpController()  # drives the pump only with --pump auto; otherwise shown in the window
    ppg_source = None
    if args.ppg == "ble":
        if args.simulate:
            ppg_source = SimPpgBle(clock, sink, stop)
        else:
            ppg_source = ppg_ble.PpgBleSource(args.ppg_address, clock, sink, stop)
        sources.append(ppg_source)
    if args.resume:
        if plot:
            for m in old_markers:  # protocol progress and marker lines in the window
                plot.add_marker(float(m["t_ms"]), m["text"])
        if steps:
            for m in old_markers:
                steps.marker(float(m["t_ms"]) / 1000, m["text"].strip())
        # the stopped time counts as a pause; a step ended only by "end" (no self-report) is continued
        code, open_step = None, False
        for m in old_markers:
            text = m["text"].strip()
            if text in protocol["conditions"]:
                code, open_step = text, True
            elif text.lower().startswith("sr:"):
                open_step = False
        add_marker("pause", float(old_end_ms))
        add_marker("resume")
        if old_markers and old_markers[-1]["text"].strip() == "end" and open_step:
            add_marker(code)
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
    print(f"Baseline from the {args.baseline_source} phase" + (" (its code is entered at its start); without "
          f"condition codes: the {args.baseline_s} s after the {args.warmup_s} s warm-up." if data_collection else "."))
    print_protocol(protocol, args.order, args.warmup_s)
    print(f"Recording to {folder}\n" + ("Type a marker + Enter at any time (in this terminal). "
                                       if data_collection else "")
          + "Ctrl+C" + (" or End in the window" if plot else "") + " to stop.")
    if esp32:
        print("Pump by hand: pump:inflate, pump:breathe, pump:off, pump:auto (back to --pump " + args.pump + ")")

    def loop():
        tick = first_tick
        while args.seconds is None or tick < first_tick + args.seconds:
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
            change = controller.update(row)
            if change and drive["mode"] == "auto":
                set_pump(*change, t=now)
            if plot:
                control = ("by hand (Auto: back to automatic)" if pump["manual"] else
                           {"auto": "automatic (closed loop)", "protocol": "protocol steps",
                            "off": "buttons only (Auto: start the closed loop)"}[drive["mode"]])
                plot.set_pump(None, None, f"control: {control}\nauto rule: {controller.status(row)}"
                              + ("" if drive["mode"] == "auto" else " (not driving)"))
            recorder.flush()
            for n in notices:
                print(f"[{now / 1000:.0f} s] {n}")
                if n.startswith("baseline ready"):
                    recorder.write_json("baseline.json", {"t_ms": now, "in_use": processor.baseline_source,
                                                          "by_source": processor.mu_by_source})
            if tick % STATUS_EVERY_S == 0:
                print(status_line(row))
            if steps and steps.code != "end" and steps.paused_since is None:
                duration = steps.duration_s()
                if duration is not None and steps.elapsed(now / 1000) >= duration:
                    nxt = steps.next_code()
                    print(f"[auto step] {'end of the protocol' if nxt == 'end' else 'next: ' + nxt}")
                    add_marker(nxt)
                    if nxt == "end":
                        break
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
        apply_pump("off", "end of session")  # the ESP32 link also sends x when it closes
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
