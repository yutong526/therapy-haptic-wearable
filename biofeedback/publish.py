"""Live output sinks. Each has publish(message) with message = {"t_ms", "state", "arousal", "valid", ...}.
run_session.py sends every message to all configured sinks.
"""

import json
import math
import socket


def _clean(v):
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 4)
    return v


class UdpJsonPublisher:
    """One JSON object per datagram, e.g. {"t_ms": 12000, "state": 1, "arousal": 0.63, "valid": true}."""

    def __init__(self, host="127.0.0.1", port=5005):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def publish(self, message):
        data = {k: ({kk: _clean(vv) for kk, vv in v.items()} if isinstance(v, dict) else _clean(v))
                for k, v in message.items()}
        try:
            self.sock.sendto(json.dumps(data).encode(), self.addr)
        except OSError:
            pass  # nobody listening is fine


class SerialCommandPublisher:
    """Sends "C,state,arousal,valid,t_ms\\n" to the ESP32 over the sensor link (Esp32Source.send).
    The line has only digits, '.', ',' and 'C', so the firmware's 't'/'r' mode switch never fires."""

    def __init__(self, esp32_source):
        self.source = esp32_source

    def publish(self, m):
        line = f"C,{m['state']},{m['arousal']:.3f},{int(m['valid'])},{int(m['t_ms'])}\n"
        self.source.send(line.encode())


class CallbackPublisher:
    """For code in the same process: calls fn(message)."""

    def __init__(self, fn):
        self.fn = fn

    def publish(self, message):
        self.fn(message)
