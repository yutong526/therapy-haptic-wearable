"""ESP32 output link: commands to the board (SerialCommandPublisher), its text lines to esp32_log.

The transport only needs open(), readline() -> str ('' on timeout), write(bytes) and close(),
so a BLE transport can replace SerialTransport.
"""

import sys
import threading

LOG_COLUMNS = ["t_ms", "line"]
QUIET_MODE = b"t"  # firmware mode without the 100 Hz raw lines; its Teleplot lines are dropped here


class SerialTransport:
    def __init__(self, port, baud=115200):
        self.port, self.baud, self.ser = port, baud, None

    def open(self):
        import serial
        # Do not toggle DTR/RTS on open: on the DevKitC UART port that would reset the board.
        self.ser = serial.serial_for_url(self.port, self.baud, timeout=0.2, do_not_open=True)
        self.ser.dtr = self.ser.rts = False
        self.ser.open()
        self.ser.reset_input_buffer()

    def readline(self):
        return self.ser.readline().decode("utf-8", errors="replace").strip()

    def write(self, data):
        self.ser.write(data)

    def close(self):
        if self.ser is not None:
            self.ser.close()


class Esp32Source(threading.Thread):
    """Keeps the link open (reconnects) and logs what the board prints."""

    def __init__(self, transport, clock, sink, stop, retry_s=3):
        super().__init__(daemon=True, name="esp32")
        self.transport, self.clock, self.sink, self.stop, self.retry_s = transport, clock, sink, stop, retry_s
        self.connected = False
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

    def run(self):
        while not self.stop.is_set():
            try:
                self.transport.open()
                self.connected = True
                self.send(QUIET_MODE)
                print("[ESP32] connected")
                while not self.stop.is_set():
                    line = self.transport.readline()
                    if not line or line.startswith(">"):
                        continue
                    if line.startswith("PPG ready"):  # board restarted in its default mode
                        self.send(QUIET_MODE)
                    self.sink("esp32_log", self.clock.ms(), [line])
            except Exception as exc:
                print(f"[ESP32] {exc}; retrying in {self.retry_s:g} s", file=sys.stderr)
            finally:
                self.connected = False
                try:
                    self.transport.close()
                except Exception:
                    pass
            self.stop.wait(self.retry_s)
