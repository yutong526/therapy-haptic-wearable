"""Connect to the RT7015 over BLE on its own and show what arrives (hardware check, nothing saved).

Prints once per second: samples received (should be ~ the sample rate), AOUT/SOUT range, bad
frames, and every beat with its IBI and whether our valid-beat rule accepts it.
Ctrl+C to stop. Examples:
    python tools/ppg_check.py                              # scan for a name containing "RT7015"
    python tools/ppg_check.py --address 70:15:88:88:88:88
    python tools/ppg_check.py --simulate
"""

import argparse
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402
from biofeedback.clock import Clock  # noqa: E402
from biofeedback.features import HeartRate  # noqa: E402
from biofeedback.ppg_ble import PpgBleSource  # noqa: E402
from biofeedback.sim import SimPpgBle  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", default=S.PPG_BLE_ADDRESS, help="BLE address (default: scan by name)")
    ap.add_argument("--simulate", action="store_true", help="simulated RT7015")
    args = ap.parse_args()

    clock, stop, lock = Clock(), threading.Event(), threading.Lock()
    heart = HeartRate()
    second = {"n": 0, "aout": [], "sout": []}

    def sink(stream, t, values):
        with lock:
            if stream == "ppg":
                second["n"] += 1
                second["aout"].append(values[0])
                second["sout"].append(values[1])
            elif stream == "beats":
                ok = heart.add(t, values[1])
                print(f"  beat {t / 1000:7.2f} s  IBI {values[1]:5d} ms  AOUT change {values[3]:6.0f} mV  "
                      f"{'valid' if ok else 'not valid'}")

    source = SimPpgBle(clock, sink, stop) if args.simulate else PpgBleSource(args.address, clock, sink, stop)
    source.start()
    print("Connecting... Ctrl+C to stop.")
    tick = 1
    try:
        while True:
            stop.wait(max(0, tick * 1000 - clock.ms()) / 1000)
            with lock:
                n, a, s = second["n"], second["aout"], second["sout"]
                second.update(n=0, aout=[], sout=[])
                hr = heart.compute(tick * 1000)["hr"]
            parser = source.stream.parser
            if n:
                print(f"{tick:4d} s | {n:4d} samples ({source.stream.clock.rate} Hz) | AOUT {min(a):6.0f}-{max(a):6.0f} mV "
                      f"| SOUT {min(s):4.0f}-{max(s):4.0f} mV | HR {'-' if hr != hr else f'{hr:.0f}'} "
                      f"| bad frames {parser.bad_frames}")
            else:
                print(f"{tick:4d} s | no data")
            tick += 1
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        source.join(timeout=5)


if __name__ == "__main__":
    main()
