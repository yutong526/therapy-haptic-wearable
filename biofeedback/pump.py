"""Closed-loop airbag pump: on / off from the 1 Hz state (settings PUMP_*), used by run_session.py --pump auto.

The same rule runs offline on a features / replay CSV (pump_times), to see when it would have fired.
"""

from . import settings as S
from .pipeline import STATE_NAMES


class PumpController:
    def __init__(self):
        self.on = False
        self.start_run = 0      # valid seconds in a row in a start state
        self.stop_run = 0       # valid seconds of a stop state since the last other valid state
        self.on_at = None
        self.off_at = None

    def update(self, row):
        """row: one features row (t_ms, phase, state, valid). Returns None or (on, reason) on a change."""
        t, state, valid = float(row["t_ms"]), int(row["state"]), bool(int(row["valid"]))
        if row["phase"] != "run":
            self.start_run = 0
            valid = False  # no baseline yet: nothing counts
        if not self.on:
            self.start_run = self.start_run + 1 if valid and state in S.PUMP_START_STATES else 0
            locked = self.off_at is not None and t - self.off_at < S.PUMP_LOCKOUT_S * 1000
            if self.start_run >= S.PUMP_START_S and not locked:
                self.on, self.on_at, self.stop_run = True, t, 0
                return True, f"{STATE_NAMES[state]} for {self.start_run} s"
            return None
        if valid:
            self.stop_run = self.stop_run + 1 if state in S.PUMP_STOP_STATES else 0
        on_s = (t - self.on_at) / 1000
        if on_s >= S.PUMP_MAX_ON_S:
            reason = f"maximum {S.PUMP_MAX_ON_S} s on"
        elif on_s >= S.PUMP_MIN_ON_S and self.stop_run >= S.PUMP_STOP_S:
            reason = f"steady / state_recovery for {self.stop_run} s"
        else:
            return None
        self.on, self.off_at, self.start_run = False, t, 0
        return False, reason

    def status(self, row):
        """One line for the live window: what the rule waits for (after update with the same row)."""
        t = float(row["t_ms"])
        if row["phase"] != "run":
            return "baseline not ready: no automatic start yet"
        if not self.on:
            if self.off_at is not None and t - self.off_at < S.PUMP_LOCKOUT_S * 1000:
                return f"lockout after the last stop: {S.PUMP_LOCKOUT_S - (t - self.off_at) / 1000:.0f} s left"
            return f"on after {S.PUMP_START_S} valid s of rise / spike: {self.start_run}/{S.PUMP_START_S}"
        return (f"on {(t - self.on_at) / 1000:.0f} s; off after {S.PUMP_STOP_S} valid s of steady / "
                f"state_recovery: {self.stop_run}/{S.PUMP_STOP_S} (on {S.PUMP_MIN_ON_S}-{S.PUMP_MAX_ON_S} s)")


def pump_times(rows):
    """[(t_ms, on, reason)] of the changes over a list of features rows."""
    controller, out = PumpController(), []
    for row in rows:
        change = controller.update(row)
        if change:
            out.append((float(row["t_ms"]), *change))
    return out
