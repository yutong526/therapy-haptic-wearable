"""Live plot of the ESP32 (PPG + IMU) and, optionally, the BITalino EDA, in one window.

Shows the last WINDOW_S seconds plus the latest B/E/P/S lines. Close the window or press
Ctrl+C to stop everything. Examples:
    python tools/live_plot.py --port COM5
    python tools/live_plot.py --port COM5 --eda COM13 --out logs/session1

--out DIR saves DIR/esp32.csv (same format as serial_logger.py: pc_ms,pc_utc,line) and,
with --eda, DIR/eda.csv (written by Input/record_eda.py, unchanged format).
"""

import argparse
import collections
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import serial
from matplotlib.animation import FuncAnimation

WINDOW_S = 10
MAX_POINTS = WINDOW_S * 200  # per series; older points are also dropped by time
WARN_SHOW_S = 3  # how long a WARN/ERROR line stays highlighted
BPM_YMIN, BPM_YMAX = 50, 110  # heart-rate axis shows at least this range
EDA_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Input", "record_eda.py")
EDA_LINE = re.compile(r"\s*([\d.]+) s \| A\d \| EDA raw ADC: \d+ \| ([\d.]+) uS")  # record_eda.py sample print

# Runs record_eda.py so that Ctrl+Break (sent on exit) raises KeyboardInterrupt,
# which lets it stop the board and close the file cleanly.
EDA_WRAPPER = (
    "import runpy, signal, sys\n"
    "if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.default_int_handler)\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)

# (panel title, series names)
PANELS = [
    ("PPG filtered (mV)", ["ppg_filt"]),
    ("PPG raw / SOUT (mV)", ["ppg_mv", "sout_mv"]),
    ("Heart rate (bpm)", ["bpm", "sout_bpm"]),  # bpm: B lines (algorithm), sout_bpm: E lines
    ("Accel (g)", ["ax", "ay", "az", "acc_mag"]),
    ("Gyro (deg/s)", ["gx", "gy", "gz"]),
]
EDA_PANEL = ("EDA (uS, BITalino)", ["eda"])


class Data:
    """Thread-safe store of recent samples and status lines."""

    def __init__(self):
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.series = collections.defaultdict(lambda: collections.deque(maxlen=MAX_POINTS))
        self.status = {}  # 'B'/'E'/'P'/'S' -> latest line, 'D' -> latest EDA message
        self.warn = (0.0, "")  # (time, line)
        self.eda_t0 = None  # maps record_eda's nominal seconds onto our time axis

    def now(self):
        return time.monotonic() - self.t0

    def feed(self, line):
        t = self.now()
        with self.lock:
            if line.startswith(">"):
                name, _, val = line[1:].partition(":")
                try:
                    self.series[name].append((t, float(val)))
                except ValueError:
                    pass
            elif line[:2] in ("B,", "E,", "P,", "S,"):
                self.status[line[0]] = line
                # B,t_ms,ibi_ms,bpm and E,t_ms,ibi_ms,bpm,high_ms; bpm 0 = no stable value
                if line[0] in "BE":
                    try:
                        bpm = float(line.split(",")[3])
                        if bpm > 0:
                            self.series["bpm" if line[0] == "B" else "sout_bpm"].append((t, bpm))
                    except (IndexError, ValueError):
                        pass
            elif line.startswith(("WARN", "ERROR")):
                self.warn = (t, line)

    def feed_eda(self, nominal_s, value):
        # EDA arrives in batches; spread samples using their nominal time instead of receive time
        now = self.now()
        with self.lock:
            if self.eda_t0 is None or abs(self.eda_t0 + nominal_s - now) > 2:  # start or reconnect
                self.eda_t0 = now - nominal_s
            self.series["eda"].append((self.eda_t0 + nominal_s, value))


def read_loop(ser, data, stop, log):
    while not stop.is_set():
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if log:
            log.write(f"{int(data.now() * 1000)},{datetime.now(timezone.utc).isoformat()},{line}\n")
        data.feed(line)


def eda_loop(proc, data):
    for line in proc.stdout:
        line = line.rstrip()
        m = EDA_LINE.match(line)
        if m:
            data.feed_eda(float(m[1]), float(m[2]))
        elif line:
            print("[EDA]", line, flush=True)
            with data.lock:
                data.status["D"] = "EDA: " + line


def start_eda(port, channel, out_path):
    cmd = [sys.executable, "-u", "-c", EDA_WRAPPER, os.path.abspath(EDA_SCRIPT),
           "--port", port, "--channel", str(channel), "--rate", "100"]
    if out_path:
        cmd += ["--output", os.path.abspath(out_path)]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)


def stop_eda(proc):
    if proc.poll() is not None:
        return
    proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def make_figure(data, panels=PANELS):
    fig, axes = plt.subplots(len(panels), 1, sharex=True, figsize=(11, 1.7 * len(panels) + 1.5))
    fig.subplots_adjust(left=0.07, right=0.88, top=0.97, bottom=0.17, hspace=0.4)
    lines = {}
    for ax, (title, names) in zip(axes, panels):
        ax.set_title(title, fontsize=9, loc="left")
        for name in names:
            style = {"marker": "o", "ms": 4} if name.endswith("bpm") else {}
            lines[name], = ax.plot([], [], lw=1, label=name, **style)
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8)  # outside, right
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("time (s)")
    bpm_ax = next(ax for ax, (_, names) in zip(axes, panels) if "bpm" in names)
    status_keys = "BEPS" + ("D" if any("eda" in names for _, names in panels) else "")
    status = fig.text(0.01, 0.115, "", fontsize=9, family="monospace", va="top")
    warn = fig.text(0.01, 0.01, "", fontsize=9, family="monospace", color="red")

    def update(_frame):
        with data.lock:
            snap = {name: list(d) for name, d in data.series.items()}
            st, (warn_t, warn_line) = dict(data.status), data.warn
        now = data.now()
        for name, ln in lines.items():
            pts = [p for p in snap.get(name, []) if p[0] >= now - WINDOW_S]
            ln.set_data([p[0] for p in pts], [p[1] for p in pts])
        # Latest algorithm value, including 0 (= no stable heart rate)
        latest = st.get("B", "").split(",")
        bpm_ax.set_title("Heart rate (bpm)  latest: " + (latest[3] if len(latest) > 3 else "-"),
                         fontsize=9, loc="left")
        for ax in axes:
            ax.relim()
            ax.autoscale_view(scalex=False)
        lo, hi = bpm_ax.get_ylim()
        bpm_ax.set_ylim(min(lo, BPM_YMIN), max(hi, BPM_YMAX))  # small changes should look small
        axes[0].set_xlim(max(0, now - WINDOW_S), max(WINDOW_S, now))
        status.set_text("\n".join(st.get(k, f"{'EDA' if k == 'D' else k}: (waiting)") for k in status_keys))
        warn.set_text(warn_line if now - warn_t < WARN_SHOW_S else "")
        return []

    return fig, update


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--eda", help="BITalino serial port, e.g. COM13 (optional)")
    ap.add_argument("--eda-channel", type=int, default=1, help="BITalino analog input (default A1)")
    ap.add_argument("--out", help="folder to save esp32.csv (and eda.csv)")
    args = ap.parse_args()

    log = None
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        log = open(os.path.join(args.out, "esp32.csv"), "w", encoding="utf-8", newline="", buffering=1)
        log.write("pc_ms,pc_utc,line\n")

    # Do not toggle DTR/RTS on open: that would reset the board.
    ser = serial.serial_for_url(args.port, args.baud, timeout=0.2, do_not_open=True)
    ser.dtr = ser.rts = False
    ser.open()
    ser.reset_input_buffer()
    ser.write(b"t")  # make sure the board is in Teleplot mode

    data, stop = Data(), threading.Event()
    reader = threading.Thread(target=read_loop, args=(ser, data, stop, log), daemon=True)
    reader.start()

    eda = None
    panels = list(PANELS)
    if args.eda:
        eda = start_eda(args.eda, args.eda_channel, args.out and os.path.join(args.out, "eda.csv"))
        threading.Thread(target=eda_loop, args=(eda, data), daemon=True).start()
        panels.insert(3, EDA_PANEL)

    fig, update = make_figure(data, panels)
    fig.canvas.manager.set_window_title(f"Live plot - {args.port}" + (f" + EDA {args.eda}" if eda else ""))
    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # noqa: F841 (keep reference)
    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        reader.join(timeout=1)
        ser.close()
        if eda:
            stop_eda(eda)
        if log:
            log.close()
            print(f"Saved ESP32 data to {os.path.abspath(log.name)}")


if __name__ == "__main__":
    main()
