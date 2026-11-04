"""ESP32 airbag pump link (firmware/pump): pump on/off commands to the board, its text lines to esp32_log.

Every HEARTBEAT_S the board gets "h" (heartbeat) and the wanted pump state, "s" (on) or "x" (off).
The firmware ignores repeats, starts only from idle and stops (deflates) when the heartbeat is
missing, so a lost command or a board restart is corrected within a second. At the end "x" is sent.
setup (bytes, e.g. the pressure targets "e1.00\ni3.00\n") is sent on connect and after a board restart,
then the mode line of the last set_pump (e.g. "b\n" breathing, "c1.00\n" constant pressure).
The transport only needs open(), readline() -> str ('' on timeout), write(bytes) and close(),
so a BLE transport can replace SerialTransport.
"""

import sys
import threading
import time

LOG_COLUMNS = ["t_ms", "line"]
HEARTBEAT_S = 1
LINE_STARTS = ("P,", "F,", "# ")  # what firmware/pump prints


def clean_line(text):
    """Drops non-printable bytes and garbage before the last line start (bursts of 0xFF and broken
    line fragments were seen on the USB serial link, 2026-11-03)."""
    text = "".join(ch for ch in text if " " <= ch <= "~").strip()
    if text.startswith(LINE_STARTS):
        return text
    i = max(text.rfind(s) for s in LINE_STARTS)
    return text[i:] if i > 0 else text


class SerialTransport:
    def __init__(self, port, baud=115200):
        self.port, self.baud, self.ser = port, baud, None

    def open(self):
        import serial
        # Do not toggle DTR/RTS on open: on the DevKit that would reset the board.
        self.ser = serial.serial_for_url(self.port, self.baud, timeout=0.2, do_not_open=True)
        self.ser.dtr = self.ser.rts = False
        self.ser.open()
        self.ser.reset_input_buffer()

    def readline(self):
        return clean_line(self.ser.readline().decode("ascii", errors="replace"))

    def write(self, data):
        self.ser.write(data)

    def close(self):
        if self.ser is not None:
            self.ser.close()


class Esp32Source(threading.Thread):
    """Keeps the link open (reconnects), sends the heartbeat and pump state, logs what the board prints."""

    def __init__(self, transport, clock, sink, stop, retry_s=3, setup=b""):
        super().__init__(daemon=True, name="esp32")
        self.transport, self.clock, self.sink, self.stop, self.retry_s = transport, clock, sink, stop, retry_s
        self.setup = b"\n" + setup  # newline first: ends any half-received line on the board
        self.connected = False
        self.pump_on = False
        self.mode = b""  # firmware mode line, sent before "s" and again after a board restart
        self.write_lock = threading.Lock()

    def send(self, data):
        """Writes to the board (e.g. output commands); dropped while disconnected."""
        if not self.connected:
            return
        try:
            with self.write_lock:
                self.transport.write(data)
        except Exception:
            pass  # the read loop notices the broken link and reconnects

    def set_pump(self, on, mode=b""):
        """on: start (or keep running) in mode; the firmware switches modes while running."""
        self.pump_on = on
        if on and mode:
            self.mode = mode
            self.send(b"\n" + mode + b"s")
        else:
            self.send(b"s" if on else b"x")

    def run(self):
        while not self.stop.is_set():
            try:
                self.transport.open()
                self.connected = True
                print("[ESP32] connected")
                self.send(self.setup + self.mode)
                last = 0.0
                while not self.stop.is_set():
                    if time.monotonic() - last >= HEARTBEAT_S:
                        last = time.monotonic()
                        self.send(b"h" + (b"s" if self.pump_on else b"x"))
                    line = self.transport.readline()
                    if line:
                        self.sink("esp32_log", self.clock.ms(), [line])
                        if line.startswith("# ESP32 airbag pump"):  # board restarted: defaults again
                            self.send(self.setup + self.mode)
                self.send(b"x")  # end of session: pump off
            except Exception as exc:
                print(f"[ESP32] {exc}; retrying in {self.retry_s:g} s", file=sys.stderr)
            finally:
                self.connected = False
                try:
                    self.transport.close()
                except Exception:
                    pass
            self.stop.wait(self.retry_s)
