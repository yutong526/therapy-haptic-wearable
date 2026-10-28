"""Session folder with one CSV per stream. The first column of every file is t_ms (shared Clock)."""

import csv
import json
import threading
from pathlib import Path


def _fmt(v):
    if isinstance(v, bool):
        return int(v)
    return round(v, 4) if isinstance(v, float) else v


class Recorder:
    def __init__(self, folder, clock, meta):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)  # never overwrite a session
        self._files = {}
        self._lock = threading.Lock()
        meta = dict(meta, start_utc=clock.start_utc.isoformat(),
                    note="t_ms in every CSV = milliseconds since start_utc, laptop monotonic clock")
        (self.folder / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def write_json(self, name, data):
        (self.folder / name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_stream(self, name, columns):
        f = open(self.folder / f"{name}.csv", "w", newline="", encoding="utf-8")
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
