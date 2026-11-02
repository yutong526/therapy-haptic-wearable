"""Session folder with one CSV per stream. The first column of every file is t_ms (shared Clock)."""

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


def _fmt(v):
    if isinstance(v, bool):
        return int(v)
    return round(v, 4) if isinstance(v, float) else v


class Recorder:
    def __init__(self, folder, clock, meta, resume=False):
        """resume: append to an existing session (its session.json is kept, the resume time added)."""
        self.folder = Path(folder)
        self.resume = resume
        self._files = {}
        self._lock = threading.Lock()
        if resume:
            meta = json.loads((self.folder / "session.json").read_text(encoding="utf-8"))
            meta.setdefault("resumed_utc", []).append(datetime.now(timezone.utc).isoformat())
        else:
            self.folder.mkdir(parents=True, exist_ok=False)  # never overwrite a session
            meta = dict(meta, start_utc=clock.start_utc.isoformat(),
                        note="t_ms in every CSV = milliseconds since start_utc, laptop monotonic clock")
        (self.folder / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def write_json(self, name, data):
        (self.folder / name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_stream(self, name, columns):
        path = self.folder / f"{name}.csv"
        if self.resume and path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), None)
            if header != columns:
                raise ValueError(f"{path}: columns differ from this version of the program, cannot resume")
            f = open(path, "a", newline="", encoding="utf-8")
            writer = csv.writer(f)
        else:
            f = open(path, "w", newline="", encoding="utf-8")
            writer = csv.writer(f)
            writer.writerow(columns)
        self._files[name] = (f, writer)

    def write(self, name, t_ms, values):
        """Streams without a file (e.g. motion, which is also in esp32_log) are ignored."""
        with self._lock:
            if name in self._files:
                self._files[name][1].writerow([round(t_ms, 1)] + [_fmt(v) for v in values])

    def flush(self):
        with self._lock:
            for f, _ in self._files.values():
                f.flush()

    def close(self):
        with self._lock:
            for f, _ in self._files.values():
                f.close()
            self._files.clear()
