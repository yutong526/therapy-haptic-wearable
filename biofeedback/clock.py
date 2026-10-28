"""One laptop clock shared by all sources, so every stream can be aligned offline."""

import time
from datetime import datetime, timezone


class Clock:
    def __init__(self):
        self.start_utc = datetime.now(timezone.utc)
        self._t0 = time.monotonic_ns()

    def ms(self):
        """Milliseconds since session start (monotonic, unaffected by clock changes)."""
        return (time.monotonic_ns() - self._t0) / 1e6
