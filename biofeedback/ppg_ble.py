"""PPG source: the RT7015 pulse module's own BLE link (bleak, asyncio in its own thread, no GUI).

Emits ("ppg", t_ms, [aout_mv, sout_mv]) for every sample and ("beats", t_ms, [confirm_ms, ibi_ms,
bpm, aout_change_mv]) for every accepted beat (t_ms = rising edge of SOUT, confirm_ms = the
falling edge where it was accepted). PpgStream holds everything after the radio (frame parsing,
sample times, beat detection) so the simulation runs the same code, and replay runs the same
BeatDetector on ppg.csv.

Frames from the module: A5, len, addr, len ^ addr, payload (len - 2 bytes), 5A.
    addr 0x11: little-endian uint16, AOUT and SOUT alternating, / 10 -> mV
    addr 0x10: payload[0] = sample-rate index into PPG_RATES
"""

import asyncio
import struct
import sys
import threading

from . import settings as S

PPG_COLUMNS = ["t_ms", "aout_mv", "sout_mv"]
BEAT_COLUMNS = ["t_ms", "confirm_ms", "ibi_ms", "bpm", "aout_change_mv"]
ADDR_RATE, ADDR_SAMPLES = 0x10, 0x11


class FrameParser:
    """Byte stream -> [(addr, payload)]. Bad frames are dropped and counted."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_frames = 0     # header found but tail or content wrong
        self.skipped_bytes = 0  # bytes discarded while searching for a header

    def reset(self):
        self.buf.clear()

    def feed(self, data):
        self.buf += data
        frames, buf = [], self.buf
        while len(buf) >= 5:
            if buf[0] != 0xA5 or buf[3] != buf[1] ^ buf[2] or buf[1] < 2:
                del buf[0]
                self.skipped_bytes += 1
                continue
            end = buf[1] + 3
            if len(buf) < end:
                break  # rest of the frame comes with the next notification
            if buf[end - 1] != 0x5A:
                del buf[0]
                self.bad_frames += 1
                continue
            frames.append((buf[2], bytes(buf[4:end - 1])))
            del buf[:end]
        return frames

    def samples(self, payload):
        """[(aout_mv, sout_mv)] from a 0x11 payload, or None if it is not whole pairs."""
        if not payload or len(payload) % 4:
            self.bad_frames += 1
            return None
        v = struct.unpack(f"<{len(payload) // 2}H", payload)
        return [(a / 10, s / 10) for a, s in zip(v[::2], v[1::2])]


class SampleClock:
    """Laptop times for the samples of each packet.

    Sample n of a run is at anchor + n / rate (sample count is the main clock). The anchor is set
    so a packet's last sample falls on its receive time, then follows the earliest arrivals: an
    early packet moves it back at once (less latency than assumed), a late one forwards by
    PPG_CLOCK_GAIN of the difference (slow drift between the two clocks; jitter mostly averages out).
    A packet more than PPG_RESYNC_MS late (lost packets, stall) re-anchors, leaving a gap.
    """

    def __init__(self, rate=S.PPG_DEFAULT_RATE):
        self.rate = rate
        self.reset()

    def reset(self):
        self.anchor, self.n, self.last_t = None, 0, None

    def set_rate(self, rate):
        if rate != self.rate:
            self.rate = rate
            self.anchor = None  # times of the new run start from the next packet

    def place(self, count, recv):
        period = 1000 / self.rate
        if self.anchor is None:
            self.n = 0
            self.anchor = recv - (count - 1) * period
        else:
            err = recv - (self.anchor + (self.n + count - 1) * period)
            if err < 0 or err > S.PPG_RESYNC_MS:
                self.anchor += err
            else:
                self.anchor += S.PPG_CLOCK_GAIN * err
        times = []
        for i in range(count):
            t = self.anchor + (self.n + i) * period
            if self.last_t is not None and t <= self.last_t:
                t = self.last_t + 0.1  # an early packet must not move samples back in time
            times.append(t)
            self.last_t = t
        self.n += count
        return times


class BeatDetector:
    """Vendor rule on SOUT/AOUT (thresholds in settings.py). IBI = time since the previous accepted
    beat, 0 if a candidate was rejected or the samples had a gap in between (chain broken)."""

    def __init__(self):
        self.reset()
        self.last_t = None

    def reset(self):
        self.high = None
        self.rise = None        # (t, aout) at the rising edge
        self.last_beat = None   # t of the previous accepted beat

    def add(self, t, aout, sout):
        """Returns (t_beat, [confirm_ms, ibi_ms, bpm, aout_change_mv]) or None."""
        if self.last_t is not None and t - self.last_t > S.PPG_GAP_MS:
            self.reset()
        self.last_t = t
        high = sout > S.PPG_SOUT_HIGH_MV
        beat = None
        if self.high is False and high:
            self.rise = (t, aout)
        elif self.high and not high and self.rise is not None:
            change = aout - self.rise[1]
            if change > S.PPG_MIN_PULSE_MV:
                t_beat = self.rise[0]
                ibi = round(t_beat - self.last_beat) if self.last_beat is not None else 0
                beat = (t_beat, [t, ibi, round(60000 / ibi) if ibi else 0, round(change, 1)])
                self.last_beat = t_beat
            else:
                self.last_beat = None
            self.rise = None
        self.high = high
        return beat


def detect_beats(samples):
    """Offline/replay: [(t, aout, sout)] -> [(t_beat, values)], same rule as live."""
    detector = BeatDetector()
    return [b for b in (detector.add(*s) for s in samples) if b]


class PpgStream:
    """Notification bytes -> ppg samples and beats on the sink. Shared by live BLE and simulation."""

    def __init__(self, sink):
        self.sink = sink
        self.parser, self.clock, self.detector = FrameParser(), SampleClock(), BeatDetector()

    def packet(self, recv, data):
        for addr, payload in self.parser.feed(data):
            if addr == ADDR_RATE:
                if payload and payload[0] < len(S.PPG_RATES):
                    self.clock.set_rate(S.PPG_RATES[payload[0]])
                else:
                    self.parser.bad_frames += 1
            elif addr == ADDR_SAMPLES:
                samples = self.parser.samples(payload)
                if not samples:
                    continue
                for t, (aout, sout) in zip(self.clock.place(len(samples), recv), samples):
                    t = round(t, 1)  # same precision as ppg.csv, so replay detects the same beats
                    self.sink("ppg", t, [aout, sout])
                    beat = self.detector.add(t, aout, sout)
                    if beat:
                        self.sink("beats", *beat)

    def disconnected(self):
        """A new connection starts a new run: no partial frame, new time anchor, no IBI across."""
        self.parser.reset()
        self.clock.reset()
        self.detector.reset()


class PpgBleSource(threading.Thread):
    """Finds the module (fixed address or name scan), subscribes, starts the stream, and reconnects
    after a disconnect or LINK_TIMEOUT_S without data. Heart rate goes invalid by itself while the
    link is down (no valid beat for HR_MAX_GAP_S)."""

    def __init__(self, address, clock, sink, stop, retry_s=S.PPG_RETRY_S):
        super().__init__(daemon=True, name="ppg_ble")
        self.address, self.clock, self.stop, self.retry_s = address, clock, stop, retry_s
        self.stream = PpgStream(sink)

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        while not self.stop.is_set():
            try:
                await self._connection()
            except Exception as exc:
                print(f"[PPG] {exc!r}", file=sys.stderr)
            self.stream.disconnected()
            if not self.stop.is_set():
                print(f"[PPG] not connected; retrying in {self.retry_s:g} s", file=sys.stderr)
                await self._sleep(self.retry_s)

    async def _sleep(self, seconds):
        end = self.clock.ms() + seconds * 1000
        while not self.stop.is_set() and self.clock.ms() < end:
            await asyncio.sleep(0.1)

    async def _find(self):
        from bleak import BleakScanner
        if self.address:
            return await BleakScanner.find_device_by_address(self.address, timeout=S.PPG_SCAN_S)
        return await BleakScanner.find_device_by_filter(
            lambda d, adv: S.PPG_BLE_NAME in (d.name or adv.local_name or ""), timeout=S.PPG_SCAN_S)

    async def _connection(self):
        from bleak import BleakClient
        device = await self._find()
        if device is None:
            print(f"[PPG] {self.address or S.PPG_BLE_NAME} not found", file=sys.stderr)
            return
        lost = asyncio.Event()
        async with BleakClient(device, disconnected_callback=lambda _: lost.set()) as client:
            last = [self.clock.ms()]

            def on_notify(_, data):
                last[0] = self.clock.ms()
                self.stream.packet(last[0], bytes(data))

            print(f"[PPG] connected to {device.name} {device.address}")
            for uuid in (S.PPG_NOTIFY_UUID, S.PPG_WRITE_UUID):
                char = client.services.get_characteristic(uuid)
                print(f"[PPG]   {uuid[4:8]}: {', '.join(char.properties) if char else 'NOT FOUND on this device'}")
            await client.start_notify(S.PPG_NOTIFY_UUID, on_notify)
            await client.write_gatt_char(S.PPG_WRITE_UUID, S.PPG_START_CMD)
            print(f"[PPG] start command sent ({S.PPG_START_CMD.hex(' ')}), waiting for data")
            last[0] = self.clock.ms()
            while not self.stop.is_set() and not lost.is_set():
                await asyncio.sleep(0.2)
                if self.clock.ms() - last[0] > S.LINK_TIMEOUT_S * 1000:
                    print(f"[PPG] no data for {S.LINK_TIMEOUT_S} s, reconnecting", file=sys.stderr)
                    break
            if lost.is_set():
                print("[PPG] the device closed the connection", file=sys.stderr)
            if not lost.is_set():
                try:
                    await client.write_gatt_char(S.PPG_WRITE_UUID, S.PPG_STOP_CMD)
                except Exception:
                    pass
        print(f"[PPG] disconnected ({self.stream.parser.bad_frames} bad frames, "
              f"{self.stream.parser.skipped_bytes} bytes skipped so far)")
