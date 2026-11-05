"""Open the live window on a recorded session, frozen at time T (s), and save a screenshot.

usage: snapshot_window.py <session folder> <T seconds> <out.png> [pump_mode] [rule]
"rule": the pump box shows what the closed-loop rule (run on the recorded features) decided, not
what pump.csv recorded (for sessions where the pump was switched by hand).
"""
import csv
import ctypes
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"C:\therapy-haptic-wearable")
sys.path.insert(0, str(ROOT))
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels for the grab

from biofeedback import live_window as LW  # noqa: E402
from biofeedback import settings as S  # noqa: E402
from biofeedback.pump import PumpController  # noqa: E402

folder, T, out = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
pump_mode = sys.argv[4] if len(sys.argv) > 4 else None
BANNER = True
FROM_RULE = len(sys.argv) > 5 and sys.argv[5] == "rule"
meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
protocol = meta["protocol"]
protocol.setdefault("auto_steps", False)
try:  # condition colours as in the current protocol file (they may have changed since the recording)
    from biofeedback.protocol import load_protocol
    current = load_protocol(ROOT / meta["protocol_file"])["conditions"]
    for code, c in protocol["conditions"].items():
        if code in current:
            c["color"] = current[code]["color"]
except (KeyError, OSError):
    pass
start = datetime.fromisoformat(meta["start_utc"])
T_ms = T * 1000


class FrozenClock:
    def ms(self):
        return T_ms


class FrozenDatetime:  # the window's wall clock shows the recording time
    @staticmethod
    def now():
        return (start + timedelta(milliseconds=T_ms)).astimezone()


LW.datetime = FrozenDatetime


def num(x):
    try:
        return float(x)
    except ValueError:
        return x


def rows(name):
    p = folder / f"{name}.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


lights = {"eda": S.EDA_RATE_HZ, "acc": S.EDA_RATE_HZ, "ppg": S.PPG_DEFAULT_RATE}
win = LW.LiveWindow(FrozenClock(), f"{folder.name} ({meta.get('order')})", protocol, meta.get("order"),
                    meta.get("warmup_s") or protocol["settling_s"], lights, lambda text: None, pump_mode=pump_mode)

lo = T_ms - S.PLOT_WINDOW_S * 1000 - 2000
for stream in ("eda", "acc", "beats", "esp32_log"):
    for r in rows(stream):
        t = float(r["t_ms"])
        if lo <= t <= T_ms:
            vals = list(r.values())[1:]
            win.add(stream, t, vals if stream == "esp32_log" else [num(v) for v in vals])
for r in rows("ppg"):
    t = float(r["t_ms"])
    if T_ms - S.PLOT_PPG_WINDOW_S * 1000 - 1000 <= t <= T_ms:
        win.add("ppg", t, [float(r["aout_mv"]), float(r["sout_mv"])])
for m in rows("markers"):
    if float(m["t_ms"]) <= T_ms:
        win.add_marker(float(m["t_ms"]), m["text"])

controller = PumpController()
baseline = meta.get("baseline_source", "chat")
for r in rows("features"):
    t = float(r["t_ms"])
    if t > T_ms:
        break
    row = {k: num(v) for k, v in r.items()}
    for k in ("state", "valid"):
        row[k] = int(row[k])
    controller.update(row)
    if t >= lo:
        win.add_row(row, f"in use: {row['baseline_source']}")
changes = []
if pump_mode and FROM_RULE:
    from biofeedback.pump import pump_times
    changes = [(t, on, r) for t, on, r in pump_times(rows("features")) if t <= T_ms]
    state, reason = ("breathe", changes[-1][2]) if changes and changes[-1][1] else ("off", "")
    for t, on, r in changes:  # as run_session.py marks automatic changes
        win.add_marker(t, "pump:" + ("on" if on else "off"))
        win.set_pump("breathe" if on else "off", r, t=t)
    win.set_pump(state, reason, f"control: automatic (closed loop)\nauto rule: {controller.status(row)}")
elif pump_mode:
    state, reason, manual = "off", "", None
    for p in rows("pump"):
        if float(p["t_ms"]) <= T_ms:
            state, reason = p.get("mode") or ("breathe" if p["on"] == "1" else "off"), p["reason"]
            win.set_pump(state, reason, t=float(p["t_ms"]))
    manual = reason.startswith("manual") and state != "off"
    control = "by hand (Auto: back to automatic)" if manual else "automatic (closed loop)"
    win.set_pump(state, reason, f"control: {control}\nauto rule: {controller.status(row)}"
                 + ("" if not manual else " (not driving)"))


def keep_lights():  # sensor lights: samples arriving at the nominal rates
    while True:
        now = time.monotonic()
        with win.lock:
            for k, rate in lights.items():
                d = win.arrivals[k]
                d.clear()
                d.extend([now - i / rate for i in range(int(rate * (0.98 if k != "ppg" else 1.1)))][::-1])
        time.sleep(0.2)


def grab(stop):
    from PIL import ImageGrab
    user32 = ctypes.windll.user32
    hwnd = 0
    while not hwnd:
        time.sleep(0.1)
        hwnd = user32.FindWindowW(None, win.title)
    user32.ShowWindow(hwnd, 3)  # maximize before the panels fill
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)  # topmost, keep size / position
    user32.SetForegroundWindow(hwnd)
    time.sleep(5)
    if pump_mode and BANNER:  # pump change banner, held in a coloured half second of its flashing
        def hold_banner():
            while not stop.is_set():
                with win.lock:
                    win.pump["changed"] = time.monotonic() - 5.1
                time.sleep(0.05)
        threading.Thread(target=hold_banner, daemon=True).start()
        time.sleep(1)
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    # PrintWindow renders the window itself (no overlap / half-painted widgets)
    from PIL import Image
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetBitmapBits(bmp, len(buf), buf)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    # maximized windows hang a few pixels off screen: trim to the visible frame
    f = ctypes.wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(f), ctypes.sizeof(f))
    img.crop((f.left - r.left, f.top - r.top, f.right - r.left, f.bottom - r.top)).save(out)
    print("saved", out, img.size)
    stop.set()


import ctypes.wintypes  # noqa: E402

stop = threading.Event()
threading.Thread(target=keep_lights, daemon=True).start()
threading.Thread(target=grab, args=(stop,), daemon=True).start()
win.show(stop)
