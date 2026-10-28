"""BITalino source: EDA, plus the ACC sensor on a second input if one is given.

Emits ("eda", t_ms, [recv_ms, sample_index, sequence, gap, adc, eda_us]) and, with an ACC channel,
("acc", t_ms, [recv_ms, sample_index, adc, acc_g]) for the same samples.
"""

import sys
import threading

from . import settings as S

EDA_VCC = 3.3  # same transfer function as Input/record_eda.py (EDA sensor datasheet Rev B)
EDA_US_PER_VOLT = 1 / 0.132
EDA_COLUMNS = ["t_ms", "recv_ms", "sample_index", "sequence", "sequence_gap_mod16", "eda_raw_adc", "eda_us"]
ACC_COLUMNS = ["t_ms", "recv_ms", "sample_index", "acc_raw_adc", "acc_g"]


def acquired(channels):
    """Physical inputs (1-6) in the order the bitalino library returns their columns (sorted)."""
    return sorted({c for c in channels if c})


def channel_bits(channel, channels=None):
    """ADC resolution of an input: the first four acquired channels are 10-bit, the 5th and 6th 6-bit
    (frame format decoded by bitalino.read). With one or two channels everything is 10-bit."""
    return 10 if acquired(channels or [channel]).index(channel) < 4 else 6


def adc_to_microsiemens(adc, bits=10):
    return adc / (1 << bits) * EDA_VCC * EDA_US_PER_VOLT


def adc_to_g(adc, bits=10, cmin=S.ACC_CMIN, cmax=S.ACC_CMAX):
    """ACC datasheet: g = (ADC - Cmin) / (Cmax - Cmin) * 2 - 1, Cmin/Cmax in 10-bit counts."""
    adc10 = adc * (1 << (10 - bits))
    return (adc10 - cmin) / (cmax - cmin) * 2 - 1


class EdaSource(threading.Thread):
    """Reads the inputs in batches of 100 ms and reconnects after errors.

    The board only reports when a batch arrives, so each sample's t_ms is the batch receive
    time minus its offset inside the batch (recv_ms is kept as well).
    """

    def __init__(self, port, channel, rate, clock, sink, stop, factory, retry_s=3, acc_channel=None):
        super().__init__(daemon=True, name="eda")
        self.port, self.channel, self.acc_channel, self.rate = port, channel, acc_channel, rate
        self.clock, self.sink, self.stop, self.factory, self.retry_s = clock, sink, stop, factory, retry_s
        channels = acquired([channel, acc_channel])
        self.columns = [c - 1 for c in channels]
        self.eda_col = 5 + channels.index(channel)
        self.bits = channel_bits(channel, channels)
        if acc_channel:
            self.acc_col = 5 + channels.index(acc_channel)
            self.acc_bits = channel_bits(acc_channel, channels)
        self.batch = max(1, rate // 10)
        self.count = 0  # continues across reconnects

    def run(self):
        period_ms = 1000 / self.rate
        while not self.stop.is_set():
            device = None
            try:
                device = self.factory(self.port, 5)
                device.start(self.rate, self.columns)
                acc = f" + ACC A{self.acc_channel}" if self.acc_channel else ""
                print(f"[EDA] recording A{self.channel}{acc} at {self.rate} Hz from {self.port}")
                previous = None
                while not self.stop.is_set():
                    rows = device.read(self.batch)
                    recv = self.clock.ms()
                    for i, row in enumerate(rows):
                        sequence, adc = int(row[0]), int(row[self.eda_col])
                        gap = 0 if previous is None else (sequence - previous - 1) % 16
                        t = recv - (len(rows) - 1 - i) * period_ms
                        self.sink("eda", t, [recv, self.count, sequence, gap, adc,
                                             adc_to_microsiemens(adc, self.bits)])
                        if self.acc_channel:
                            a = int(row[self.acc_col])
                            self.sink("acc", t, [recv, self.count, a, adc_to_g(a, self.acc_bits)])
                        previous = sequence
                        self.count += 1
            except Exception as exc:
                print(f"[EDA] {exc}; retrying in {self.retry_s:g} s", file=sys.stderr)
            finally:
                if device is not None:
                    for close in (device.stop, device.close):
                        try:
                            close()
                        except Exception:
                            pass
            self.stop.wait(self.retry_s)
