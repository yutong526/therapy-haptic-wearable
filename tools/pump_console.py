"""Airbag pump console (firmware/pump): type commands, see pressure vs target live, everything logged.

For finding comfortable breathing PWMs / pressures and tuning the constant-pressure PI controller.
Commands (Enter after each): s start, x stop (deflates), c<kPa> constant pressure, b breathing,
a<pwm> / d<pwm> inhale / exhale PWM, e<kPa> / i<kPa> exhale / inhale stop pressures, p<val> / n<val> Kp / Ki, v<kPa> deflate end
pressure (e.g. v-1), z zero, ? settings.
note:<text> is only logged (e.g. note:wrist 3 kPa comfortable). A heartbeat is sent every second,
so the pump stops by itself 5 s after this program ends. Ctrl+C (or closing the plot) sends x.
Saves sessions/<date>_pump_<name>/pump_console.csv (t_ms, line; typed lines start with "> ").
Example:
    python tools/pump_console.py COM11 --name wrist
"""

import argparse
import collections
import csv
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback.esp32 import SerialTransport  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WINDOW_S = 60
PRINT_P_EVERY_S = 1  # P lines arrive at 10 Hz; the terminal shows one per second (all are logged)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="ESP32 serial port, e.g. COM11")
    ap.add_argument("--name", default="test", help="added to the folder name, e.g. the body part")
    ap.add_argument("--no-plot", action="store_true", help="terminal only")
    ap.add_argument("--out", default=str(ROOT / "sessions"))
    args = ap.parse_args()

    folder = Path(args.out) / f"{datetime.now():%Y%m%d_%H%M%S}_pump_{args.name}"
    folder.mkdir(parents=True)
    log_file = open(folder / "pump_console.csv", "w", newline="", encoding="utf-8")
    log = csv.writer(log_file)
    log.writerow(["t_ms", "line"])
    t0 = time.monotonic()
    lock = threading.Lock()
    stop = threading.Event()
    data = collections.deque()  # (t_s, kPa, target, pwm_in, pwm_out)

    def record(line):
        with lock:
            log.writerow([round((time.monotonic() - t0) * 1000, 1), line])
            log_file.flush()

    link = SerialTransport(args.port)
    link.open()
    print(f"Connected to {args.port}; logging to {folder}. Type commands (? for settings).")

    def reader():
        last_p = 0.0
        while not stop.is_set():
            line = link.readline()
            if not line:
                continue
            record(line)
            parts = line.split(",")
            if parts[0] == "P" and len(parts) == 7:
                t = time.monotonic() - t0
                try:
                    point = (t, float(parts[2]), float(parts[3]), int(parts[4]), int(parts[5]))
                except ValueError:
                    continue
                with lock:
                    data.append(point)
                    while data and data[0][0] < t - WINDOW_S:
                        data.popleft()
                if t - last_p < PRINT_P_EVERY_S:
                    continue
                last_p = t
                print(f"  {parts[6]:8} {float(parts[2]):5.2f} kPa (target {float(parts[3]):.2f}) "
                      f"pwm in {parts[4]:>3} out {parts[5]:>3}")
            else:
                print(f"  {line}")

    write_lock = threading.Lock()

    def send(data_bytes):
        with write_lock:
            link.write(data_bytes)

    def heartbeat():
        while not stop.wait(1):
            send(b"h")

    def typed():
        for line in sys.stdin:
            text = line.strip()
            if not text:
                continue
            record("> " + text)
            if not text.startswith("note:"):
                send(text.encode() + b"\n")
        stop.set()

    for fn in (reader, heartbeat, typed):
        threading.Thread(target=fn, daemon=True).start()

    try:
        if args.no_plot:
            while not stop.wait(0.5):
                pass
        else:
            plot(data, lock, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        send(b"\nx")
        record("> x (end)")
        time.sleep(0.3)
        link.close()
        log_file.close()
        print(f"Stopped (x sent). Saved {folder / 'pump_console.csv'}")


def plot(data, lock, stop):
    import matplotlib.pyplot as plt

    fig, (ax_p, ax_u) = plt.subplots(2, 1, sharex=True, figsize=(9, 5), height_ratios=[2, 1])
    line_p, = ax_p.plot([], [], color="#2a6fb0", lw=1.5, label="pressure")
    line_t, = ax_p.plot([], [], color="#9e9e9e", lw=1.2, ls="--", label="target")
    line_in, = ax_u.plot([], [], color="#2a6fb0", lw=1.2, label="inflate pump")
    line_out, = ax_u.plot([], [], color="#c0504d", lw=1.2, label="deflate pump")
    ax_p.set_title("Airbag pressure (kPa)", fontsize=9, loc="left")
    ax_u.set_title("PWM (0-255)", fontsize=9, loc="left")
    ax_u.set_ylim(-5, 260)
    ax_u.set_xlabel("seconds")
    for ax in (ax_p, ax_u):
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.grid(axis="y", color="#e6e6e6", lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    plt.show(block=False)
    while not stop.is_set() and plt.fignum_exists(fig.number):
        with lock:
            snap = list(data)
        if snap:
            t = [d[0] for d in snap]
            line_p.set_data(t, [d[1] for d in snap])
            line_t.set_data(t, [d[2] for d in snap])
            line_in.set_data(t, [d[3] for d in snap])
            line_out.set_data(t, [d[4] for d in snap])
            ax_p.set_xlim(max(0, t[-1] - WINDOW_S), max(WINDOW_S, t[-1]))
            hi = max(max(d[1] for d in snap), max(d[2] for d in snap), 1.0)
            ax_p.set_ylim(min(-0.2, min(d[1] for d in snap)), hi * 1.15)
        plt.pause(0.2)


if __name__ == "__main__":
    main()
