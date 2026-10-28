"""Show the raw BITalino ACC readings, to check the sensor and measure its calibration (Cmin/Cmax).

Prints 10 lines per second: ADC, g (with the ACC_CMIN/ACC_CMAX in biofeedback/settings.py),
the 1 s standard deviation used as the motion measure, and the lowest/highest ADC seen.
Calibration (datasheet): rotate the sensor very slowly through 360 deg so the axis points straight
down (-1 g) and straight up (+1 g), then Ctrl+C and copy the suggested values into settings.py.
Examples:
    python tools/acc_check.py --port COM15 --channel 5
"""

import argparse
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biofeedback import settings as S  # noqa: E402
from biofeedback.eda import adc_to_g  # noqa: E402

RATE = 100


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="BITalino serial port, e.g. COM15")
    ap.add_argument("--channel", type=int, required=True, choices=range(1, 7), help="input of the ACC sensor")
    args = ap.parse_args()

    from bitalino import BITalino
    device = BITalino(args.port, 5)
    device.start(RATE, [args.channel - 1])  # one channel -> 10-bit
    print(f"ACC on A{args.channel}, calibration in settings.py: Cmin {S.ACC_CMIN}, Cmax {S.ACC_CMAX}")
    print("   ADC       g   1s std (g)   min  max")
    last = collections.deque(maxlen=RATE)
    low = high = None
    try:
        while True:
            for row in device.read(RATE // 10):
                adc = int(row[5])
                last.append(adc_to_g(adc))
                low = adc if low is None else min(low, adc)
                high = adc if high is None else max(high, adc)
            std = statistics.pstdev(last) if len(last) > 1 else 0.0
            still = "still" if std <= S.MOTION_LO_G else "MOVING" if std >= S.MOTION_HI_G else ""
            print(f"{adc:6d} {last[-1]:7.3f}   {std:8.4f} {still:6} {low:5d} {high:4d}")
    except KeyboardInterrupt:
        pass
    finally:
        for close in (device.stop, device.close):
            try:
                close()
            except Exception:
                pass
    if low is not None:
        print(f"\nIf the axis pointed straight down and straight up during the run, set in biofeedback/settings.py:\n"
              f"ACC_CMIN = {low}\nACC_CMAX = {high}\n(min/max include any shaking: rotate slowly)")


if __name__ == "__main__":
    main()
