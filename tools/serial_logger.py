"""Log the board's serial output to CSV with the laptop receive time.

Each saved row is: pc_ms,pc_utc,<original line>. pc_ms counts from the start of the
recording; pc_utc uses the same ISO format as Input/record_eda.py (batch_received_utc),
so both recordings can be aligned. B, E, P, S, WARN and ERROR lines are also printed live.
Example:
    python tools/serial_logger.py --port COM5 --seconds 60 --out logs/finger.csv
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import serial

LIVE_PREFIXES = ("B,", "E,", "P,", "S,", "WARN", "ERROR", "#")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, required=True, help="recording length")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--mode", choices=["r", "t"], default="r", help="output mode sent to the board")
    args = ap.parse_args()

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Do not toggle DTR/RTS on open: on the DevKitC UART port that would reset the board.
    # serial_for_url also accepts test URLs such as loop://
    ser = serial.serial_for_url(args.port, args.baud, timeout=0.2, do_not_open=True)
    ser.dtr = ser.rts = False
    ser.open()
    ser.reset_input_buffer()
    ser.write(args.mode.encode())

    t0 = time.monotonic()
    n = 0
    try:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            f.write("pc_ms,pc_utc,line\n")
            while time.monotonic() - t0 < args.seconds:
                raw = ser.readline()
                if not raw:
                    continue
                pc_ms = int((time.monotonic() - t0) * 1000)
                pc_utc = datetime.now(timezone.utc).isoformat()
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                f.write(f"{pc_ms},{pc_utc},{line}\n")
                n += 1
                if line.startswith(LIVE_PREFIXES):
                    print(line, flush=True)
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        ser.close()
    print(f"Saved {n} lines to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
