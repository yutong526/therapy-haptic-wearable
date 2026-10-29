"""Live window for run_session.py --plot (Tkinter + matplotlib; must run in the main thread).

Left: what is being recorded (EDA, motion, PPG waveform with the detected beats, heart rate,
arousal / confidence), with the background coloured by the current condition marker.
Right: clock, current protocol step with countdown, buttons for the markers and self-reports, a
note box, sensor lights, confidence and baseline progress, and an End button.

Display and marker input only: markers go through the same function as the terminal input, and
nothing here feeds back into the processing.
"""

import bisect
import collections
import signal
import threading
import time
from datetime import datetime

from . import settings as S
from .pipeline import STATE_NAMES

SPECIAL_COLORS = {"settling": "#E0E0E0", "end": "#FFFFFF"}  # condition colours: config/protocol.yaml
LIGHT = {"ok": "#43A047", "slow": "#FDD835", "off": "#E53935", "wait": "#9E9E9E"}
MARKER_COLOR = "tab:purple"


def fmt_s(s):
    s = int(abs(s))
    return f"{s // 60:02d}:{s % 60:02d}"


class Progress:
    """Follows the markers (from the window or the terminal) through the steps of one order."""

    def __init__(self, protocol, order, settling_s):
        self.conditions = protocol["conditions"]
        self.steps = [(st["code"], st["seconds"], st["note"]) for st in protocol["orders"][order]]
        self.settling_s = settling_s
        self.index = -1          # -1 = settling, len(steps) = after the last step
        self.code = "settling"   # condition shown (may be off-protocol)
        self.start_s = 0.0

    def marker(self, t_s, text):
        if text == "end":
            self.index, self.code, self.start_s = len(self.steps), "end", t_s
        elif text in self.conditions:
            # next step with this code; otherwise an off-protocol condition at the same step
            later = [i for i in range(self.index + 1, len(self.steps)) if self.steps[i][0] == text]
            if later:
                self.index = later[0]
            self.code, self.start_s = text, t_s

    def duration_s(self):
        if self.index == -1:
            return self.settling_s
        if self.index < len(self.steps) and self.steps[self.index][0] == self.code:
            return self.steps[self.index][1]
        return None

    def note(self):
        if 0 <= self.index < len(self.steps) and self.steps[self.index][0] == self.code:
            return self.steps[self.index][2]
        return ""

    def next_code(self):
        i = self.index + 1
        return self.steps[i][0] if i < len(self.steps) else "end"


class LiveWindow:
    def __init__(self, clock, title, protocol, order, settling_s, streams, on_marker):
        """protocol: from protocol.load_protocol; streams: {name: expected samples/s} to show a light
        for; on_marker(text) records a marker."""
        self.clock, self.title, self.streams, self.on_marker = clock, title, streams, on_marker
        self.conditions, self.self_report = protocol["conditions"], protocol["self_report"]
        self.colors = dict(SPECIAL_COLORS, **{k: c["color"] for k, c in self.conditions.items()})
        self.lock = threading.Lock()
        self.series = collections.defaultdict(collections.deque)  # name -> deque of (t_s, value)
        self.markers = []      # (t_s, text)
        self.row = None        # latest features row
        self.baseline = ""     # baseline status line from the processor
        self.protocol = Progress(protocol, order, settling_s)
        self.arrivals = {k: collections.deque() for k in streams}  # laptop times of recent samples

    # Called from the source threads / the main loop / the marker input -------------------------
    def add(self, stream, t, values):
        t /= 1000
        with self.lock:
            if stream in self.arrivals:
                self._count(stream)
            if stream == "eda":
                self._push("eda", t, values[-1], S.PLOT_WINDOW_S)
            elif stream == "acc":
                self._push("acc", t, values[-1], S.PLOT_WINDOW_S)
            elif stream == "ppg" and len(values) == 2:  # BLE: [aout_mv, sout_mv]
                self._push("aout", t, values[0], S.PLOT_PPG_WINDOW_S)
            elif stream == "beats":
                self._push("beat", t, values[2], S.PLOT_WINDOW_S)  # BLE: bpm of this IBI (0 = none)

    def add_row(self, row, baseline):
        t = row["t_ms"] / 1000
        with self.lock:
            self.row, self.baseline = row, baseline
            for key in ("hr", "arousal", "c"):
                self._push(key, t, row[key], S.PLOT_WINDOW_S)

    def add_marker(self, t, text):
        with self.lock:
            self.markers.append((t / 1000, text))
            self.protocol.marker(t / 1000, text)

    def name(self, code):
        c = self.conditions.get(code)
        return c["name"] if c else code

    def _count(self, stream):
        d, now = self.arrivals[stream], time.monotonic()
        d.append(now)
        while d and d[0] < now - 1:
            d.popleft()

    def _push(self, key, t, value, window_s):
        d = self.series[key]
        d.append((t, value))
        while d and d[0][0] < t - window_s:
            d.popleft()

    # Main thread -------------------------------------------------------------------------------
    def show(self, stop):
        """Blocks until the window is closed or End is pressed (sets stop), or stop is set elsewhere."""
        import tkinter as tk
        from tkinter import messagebox

        import matplotlib
        matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        root = tk.Tk()
        root.title(self.title)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{int(sw * 0.92)}x{int(sh * 0.85)}+{int(sw * 0.04)}+10")
        scale = root.winfo_fpixels("1i") / 96  # display scaling (e.g. 1.5 at 150 %)
        wrap = int(300 * scale)

        # Right: panel (packed first so the plots cannot push it out of the window)
        panel = tk.Frame(root, width=int(340 * scale), padx=10, pady=10)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)

        # Left: plots
        fig = Figure(figsize=(10, 9))
        fig.subplots_adjust(left=0.08, right=0.98, top=0.96, bottom=0.06, hspace=0.6)
        axes = fig.subplots(5, 1)
        canvas = FigureCanvasTkAgg(fig, master=root)
        canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        draw_plots = self._plots(fig, axes)
        big = ("Segoe UI", 18, "bold")
        mid = ("Segoe UI", 11)
        small = ("Segoe UI", 9)

        def end():
            if messagebox.askyesno("End session", "Type 'end' and stop the recording?", parent=root):
                self._send("end")
                stop.set()
        # packed first so it always stays visible at the bottom
        tk.Button(panel, text="End session", font=mid, bg="#EF9A9A", command=end).pack(side=tk.BOTTOM, fill=tk.X)

        top = tk.Frame(panel)
        top.pack(fill=tk.X)
        clock_lbl = tk.Label(top, font=("Consolas", 16), anchor="w")
        clock_lbl.pack(side=tk.LEFT)
        session_lbl = tk.Label(top, font=small, anchor="e")
        session_lbl.pack(side=tk.RIGHT)

        block = tk.Frame(panel, bd=2, relief=tk.GROOVE, pady=6)
        block.pack(fill=tk.X, pady=(8, 4))
        step_lbl = tk.Label(block, font=small)
        cond_lbl = tk.Label(block, font=big)
        timer_lbl = tk.Label(block, font=("Consolas", 22, "bold"))
        hint_lbl = tk.Label(block, font=small, wraplength=wrap)
        for w in (step_lbl, cond_lbl, timer_lbl, hint_lbl):
            w.pack()

        next_btn = tk.Button(panel, font=mid, command=lambda: self._send(self.protocol.next_code()))
        next_btn.pack(fill=tk.X, pady=(4, 8))

        tk.Label(panel, text="Condition marker (also: type in terminal)", font=small, anchor="w").pack(fill=tk.X)
        row1 = tk.Frame(panel)
        row1.pack(fill=tk.X)
        for code, c in self.conditions.items():
            tk.Button(row1, text=f"{code}\n{c['name']}", bg=c["color"], font=small, width=6,
                      command=lambda c=code: self._send(c)).pack(side=tk.LEFT, expand=True, fill=tk.X)

        sr_range = self.self_report
        tk.Label(panel, text=f"Self-report sr:N ({sr_range['hint']})", font=small, anchor="w",
                 wraplength=wrap, justify=tk.LEFT).pack(fill=tk.X, pady=(8, 0))
        row2 = tk.Frame(panel)
        row2.pack(fill=tk.X)
        for n in range(sr_range["min"], sr_range["max"] + 1):
            tk.Button(row2, text=str(n), font=mid, width=3,
                      command=lambda n=n: self._send(f"sr:{n}")).pack(side=tk.LEFT, expand=True, fill=tk.X)
        sr_lbl = tk.Label(panel, font=small, anchor="w", fg="#555555")
        sr_lbl.pack(fill=tk.X)

        tk.Label(panel, text="Note (Enter to save)", font=small, anchor="w").pack(fill=tk.X, pady=(8, 0))
        note = tk.Entry(panel, font=small)
        note.pack(fill=tk.X)

        def save_note(_event=None):
            text = note.get().strip()
            if text:
                self._send(f"note:{text}")
                note.delete(0, tk.END)
        note.bind("<Return>", save_note)
        last_lbl = tk.Label(panel, font=small, anchor="w", fg="#555555")
        last_lbl.pack(fill=tk.X)

        tk.Label(panel, text="Sensors (samples/s)", font=small, anchor="w").pack(fill=tk.X, pady=(6, 0))
        lights = {}
        f = tk.Frame(panel)
        f.pack(fill=tk.X)
        for stream in self.streams:
            dot = tk.Label(f, text="●", font=("Segoe UI", 12), fg=LIGHT["wait"])
            dot.pack(side=tk.LEFT)
            txt = tk.Label(f, font=small, anchor="w")
            txt.pack(side=tk.LEFT, padx=(0, 6))
            lights[stream] = (dot, txt)
        quality_lbl = tk.Label(panel, font=small, anchor="w", justify=tk.LEFT)
        quality_lbl.pack(fill=tk.X, pady=(4, 0))

        def on_close():
            if messagebox.askyesno("Stop recording", "Stop the recording now? (no 'end' marker is added)",
                                   parent=root):
                stop.set()
        root.protocol("WM_DELETE_WINDOW", on_close)

        def tick():
            if stop.is_set():
                root.destroy()
                return
            now = self.clock.ms() / 1000
            with self.lock:
                p = self.protocol
                code, start, dur, index, step_note = p.code, p.start_s, p.duration_s(), p.index, p.note()
                nxt, steps = p.next_code(), len(p.steps)
                row, baseline = self.row, self.baseline
                sr = [text for _, text in self.markers if text.startswith("sr:")]
                last = self.markers[-1] if self.markers else None
                mono = time.monotonic()
                rates = {k: (len(d), mono - d[-1] if d else None) for k, d in self.arrivals.items()}

            clock_lbl.config(text=datetime.now().strftime("%H:%M:%S"))
            session_lbl.config(text=f"session {fmt_s(now)}")

            elapsed = now - start
            over = dur is not None and elapsed > dur
            flash = over and int(now * 2) % 2 == 0
            bg = "#FFFFFF" if flash else self.colors.get(code, "#FFFFFF")
            for w in (block, step_lbl, cond_lbl, timer_lbl, hint_lbl):
                w.config(bg=bg)
            if code == "settling":
                step_lbl.config(text="settling (not used)")
            elif code == "end":
                step_lbl.config(text="finished")
            else:
                step_lbl.config(text=f"step {index + 1}/{steps}" if 0 <= index < steps else "")
            cond_lbl.config(text=f"{code} {self.name(code)}" if code in self.conditions else self.name(code))
            if dur is None:
                timer_lbl.config(text=fmt_s(elapsed))
                hint_lbl.config(text="" if code == "end" else "off protocol: elapsed time")
            elif over:
                timer_lbl.config(text="+" + fmt_s(elapsed - dur))
                hint_lbl.config(text="time is up: self-report, then next step"
                                if code != "settling" else "time is up: start step 1")
            else:
                timer_lbl.config(text=fmt_s(dur - elapsed))
                hint_lbl.config(text=f"remaining of {fmt_s(dur)}" + (f"\n{step_note}" if step_note else ""))
            next_btn.config(text=f"Start next: {nxt} {self.name(nxt)}" if nxt != "end" else "Next: end",
                            bg=self.colors.get(nxt, "#FFFFFF"), state=tk.DISABLED if code == "end" else tk.NORMAL)
            sr_lbl.config(text="given: " + " ".join(s[3:] for s in sr) if sr else "given: -")
            if last:
                last_lbl.config(text=f"last marker {fmt_s(last[0])}: {last[1][:35]}")

            for stream, (dot, txt) in lights.items():
                n, age = rates[stream]
                expected = self.streams[stream]
                if age is None:
                    state = "wait"
                elif age > S.PLOT_STALE_S:
                    state = "off"
                elif n < 0.5 * expected:
                    state = "slow"
                else:
                    state = "ok"
                dot.config(fg=LIGHT[state])
                txt.config(text=f"{stream} {n}" + (f" (none {age:.0f} s)" if state == "off" else "")
                           if age is not None else f"{stream} -")
            if row is not None:
                quality_lbl.config(text=f"confidence c {row['c']:.2f} (eda {row['c_eda']:.0f}, "
                                        f"motion {row['c_motion']:.2f})\nbaseline {baseline}\n"
                                        f"state {STATE_NAMES[row['state']]}, arousal {row['arousal']:.2f}")

            draw_plots(now)
            canvas.draw_idle()
            root.after(S.PLOT_REFRESH_MS, tick)

        # Ctrl+C in the terminal: Tk would swallow KeyboardInterrupt in the callbacks
        old_handler = signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            root.after(S.PLOT_REFRESH_MS, tick)
            root.mainloop()
        finally:
            signal.signal(signal.SIGINT, old_handler)
            stop.set()

    def _send(self, text):
        self.on_marker(text)

    def _plots(self, fig, axes):
        """Returns draw(now_s) that updates the five panels."""
        ax_eda, ax_mot, ax_ppg, ax_hr, ax_ar = axes
        slow = [ax_eda, ax_mot, ax_hr, ax_ar]
        for ax in slow[1:]:
            ax.sharex(ax_eda)
        for ax in axes:
            ax.grid(alpha=0.3)
        ax_eda.set_title("EDA (uS)", fontsize=9, loc="left")
        ax_mot.set_title("Motion: BITalino ACC (g)", fontsize=9, loc="left")
        ax_ppg.set_title(f"PPG AOUT (mV), last {S.PLOT_PPG_WINDOW_S} s; dots = detected beats", fontsize=9, loc="left")
        ax_hr.set_title("Heart rate (bpm): line = HR used (median of valid beats), dots = each beat",
                        fontsize=9, loc="left")
        ax_ar.set_ylim(-0.05, 1.05)
        ax_ar.set_xlabel("time (s)")

        ln = {
            "eda": ax_eda.plot([], [], lw=1)[0],
            "motion": ax_mot.plot([], [], lw=1)[0],
            "aout": ax_ppg.plot([], [], lw=1)[0],
            "beat_dots": ax_ppg.plot([], [], "o", ms=5, color="tab:red")[0],
            "beat": ax_hr.plot([], [], ".", ms=4, color="tab:gray", alpha=0.6)[0],
            "hr": ax_hr.plot([], [], lw=2, color="tab:red")[0],
            "arousal": ax_ar.plot([], [], lw=2, label="arousal")[0],
            "c": ax_ar.plot([], [], lw=1, color="tab:green", label="confidence c")[0],
        }
        ax_ar.legend(loc="upper left", fontsize=8)
        drawn_markers = [0]
        bands = []  # background patches, redrawn every frame

        def xy(points):
            return [p[0] for p in points], [p[1] for p in points]

        def draw(now):
            with self.lock:
                snap = {k: list(v) for k, v in self.series.items()}
                markers, row = list(self.markers), self.row
            lo_x = max(0, now - S.PLOT_WINDOW_S)

            ln["eda"].set_data(*xy(snap.get("eda", [])))
            ln["motion"].set_data(*xy(snap.get("acc", [])))

            aout = snap.get("aout", [])
            ln["aout"].set_data(*xy(aout))
            if aout:
                ts = [p[0] for p in aout]
                dots = []
                for t, _ in snap.get("beat", []):
                    i = bisect.bisect_left(ts, t)
                    if i < len(ts) and abs(ts[i] - t) < 0.01:
                        dots.append(aout[i])
                ln["beat_dots"].set_data(*xy(dots))
                ax_ppg.set_xlim(ts[-1] - S.PLOT_PPG_WINDOW_S, ts[-1] + 0.2)

            lo_bpm, hi_bpm = 60000 / S.IBI_MAX_MS, 60000 / S.IBI_MIN_MS  # outside: never valid, would squash the axis
            ln["beat"].set_data(*xy([p for p in snap.get("beat", []) if lo_bpm <= p[1] <= hi_bpm]))
            ln["hr"].set_data(*xy(snap.get("hr", [])))
            ln["arousal"].set_data(*xy(snap.get("arousal", [])))
            ln["c"].set_data(*xy(snap.get("c", [])))

            # background = condition in force (settling until the first condition marker)
            for patch in bands:
                patch.remove()
            bands.clear()
            segments, code, start = [], "settling", 0.0
            for t, text in markers:
                if text in self.conditions or text == "end":
                    segments.append((start, t, code))
                    code, start = text, t
            segments.append((start, now, code))
            for a, b, c in segments:
                if b >= lo_x and c in self.colors:
                    for ax in slow:
                        bands.append(ax.axvspan(a, b, color=self.colors[c], alpha=0.35, lw=0, zorder=0))

            for t, text in markers[drawn_markers[0]:]:
                for ax in slow:
                    ax.axvline(t, color=MARKER_COLOR, lw=1, ls="--")
                is_condition = text in self.conditions or text == "end"
                ax_eda.text(t, 0.95 if is_condition else 0.15, " " + self.name(text)[:20], va="top" if is_condition
                            else "bottom", transform=ax_eda.get_xaxis_transform(), fontsize=8,
                            color=MARKER_COLOR, clip_on=True)  # conditions on top, sr / notes at the bottom
            drawn_markers[0] = len(markers)

            if row is not None:
                hr = row["hr"]
                ax_ar.set_title(f"{row['phase']} | state: {STATE_NAMES[row['state']]} | arousal {row['arousal']:.2f} "
                                f"| c {row['c']:.2f} | HR {'-' if hr != hr else f'{hr:.0f}'}"
                                f"{'' if row['valid'] else ' | INVALID'}", fontsize=9, loc="left")
            ax_hr.set_autoscaley_on(True)  # set_ylim below turns it off
            for ax in (ax_eda, ax_mot, ax_ppg, ax_hr):
                ax.relim()
                ax.autoscale_view(scalex=False)
            lo, hi = ax_hr.get_ylim()
            ax_hr.set_ylim(min(lo, 50), max(hi, 110))  # small changes should look small
            ax_eda.set_xlim(lo_x, max(S.PLOT_WINDOW_S, now))

        return draw
