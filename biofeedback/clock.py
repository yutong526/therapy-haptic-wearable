"""One laptop clock shared by all sources, so every stream can be aligned offline."""

import time
from datetime import datetime, timezone


class Clock:
    def __init__(self, start_utc=None):
        """start_utc: continue the time axis of an earlier session (run_session.py --resume)."""
        now = datetime.now(timezone.utc)
        self.start_utc = start_utc or now
        self._offset_ms = (now - self.start_utc).total_seconds() * 1000
        self._t0 = time.monotonic_ns()

    def ms(self):
        """Milliseconds since session start (monotonic, unaffected by clock changes)."""
        return self._offset_ms + (time.monotonic_ns() - self._t0) / 1e6
