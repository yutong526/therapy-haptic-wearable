"""ESP32 source: PPG and IMU arrive on one line stream and are split into separate streams.

The transport only needs open(), readline() -> str ('' on timeout), write(bytes) and close(),
so a BLE transport can replace SerialTransport without touching the parsing.

Modes (sent to the firmware as one character):
    record: 'r' -> R lines at 100 Hz (raw PPG + IMU, for offline work) plus B/E/P/S
    live:   't' -> Teleplot lines (dropped here) plus B/E/P/S; the processing only needs B and S
"""

import sys
import threading

PPG_COLUMNS = ["t_ms", "board_ms", "ppg_mv", "sout_mv"]
IMU_COLUMNS = ["t_ms", "board_ms", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"]
BEAT_COLUMNS = ["t_ms", "board_ms", "ibi_ms", "bpm"]
LOG_COLUMNS = ["t_ms", "line"]
MODE_CHAR = {"record": b"r", "live": b"t"}


def parse_line(line):
    """Splits one firmware line (src/main.cpp) into [(stream, values)].

    R,board_ms,ppg_mv,sout_mv,ax,ay,az,gx,gy,gz -> ppg + imu (IMU values are nan if the read failed)
    B,board_ms,ibi_ms,bpm                       -> beats (ibi 0 = first/invalid interval)
    S,board_ms,acc_mean_g,motion_rms_g,gyro_rms -> motion (not saved separately) + esp32_log
    Teleplot lines (">name:value") are dropped; everything else goes to esp32_log.
    """
    parts = line.split(",")
    try:
        if parts[0] == "R" and len(parts) == 10:
            board_ms, v = int(parts[1]), [float(p) for p in parts[2:]]
            return [("ppg", [board_ms] + v[:2]), ("imu", [board_ms] + v[2:])]
        if parts[0] == "B" and len(parts) == 4:
            return [("beats", [int(parts[1]), int(parts[2]), int(parts[3])])]
        if parts[0] == "S" and len(parts) == 5:
            return [("motion", [int(parts[1])] + [float(p) for p in parts[2:]]), ("esp32_log", [line])]
    except ValueError:
        pass
    if line.startswith(">"):
        return []
    return [("esp32_log", [line])]


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
    def __init__(self, transport, clock, sink, stop, mode="record", retry_s=3):
        super().__init__(daemon=True, name="esp32")
        self.transport, self.clock, self.sink, self.stop, self.retry_s = transport, clock, sink, stop, retry_s
        self.mode = MODE_CHAR[mode]
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
                self.send(self.mode)
                print("[ESP32] connected")
                while not self.stop.is_set():
                    line = self.transport.readline()
                    if not line:
                        continue
                    t = self.clock.ms()
                    if line.startswith("PPG ready"):  # board restarted in its default mode
                        self.send(self.mode)
                    for stream, values in parse_line(line):
                        self.sink(stream, t, values)
            except Exception as exc:
                print(f"[ESP32] {exc}; retrying in {self.retry_s:g} s", file=sys.stderr)
            finally:
                self.connected = False
                try:
                    self.transport.close()
                except Exception:
                    pass
            self.stop.wait(self.retry_s)
