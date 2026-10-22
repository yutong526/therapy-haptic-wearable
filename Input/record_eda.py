"""Stream one BITalino analog input to CSV (or simulate without hardware)."""

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
import sys


def record(args, device=None):
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    previous = None
    started = False
    # Exclusive creation protects earlier recordings from accidental overwrite.
    with target.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "sample_index", "nominal_elapsed_seconds", "sequence", "sequence_gap_mod16",
            "eda_raw_adc", "analog_input", "sampling_rate_hz", "batch_received_utc",
        ])
        stream.flush()
        try:
            if device is not None:
                device.start(args.rate, [args.channel - 1])
                started = True
            print(f"Recording A{args.channel} at {args.rate} Hz to {target.resolve()}")
            print("Press Ctrl+C to stop.")
            total = None if args.duration is None else math.ceil(args.duration * args.rate)
            batch_size = max(1, args.rate // 10)
            while total is None or count < total:
                size = batch_size if total is None else min(batch_size, total - count)
                if device is None:
                    bits = 10 if args.channel <= 4 else 6
                    rows = [[(count + i) % 16, 0, 0, 0, 0,
                             int(((1 << bits) - 1) * (0.5 + 0.1 * math.sin((count + i) / args.rate)))]
                            for i in range(size)]
                else:
                    rows = device.read(size)
                received = datetime.now(timezone.utc).isoformat()
                if len(rows) != size:
                    raise RuntimeError(f"Expected {size} samples; received {len(rows)}")
                for row in rows:
                    sequence = int(row[0])
                    gap = 0 if previous is None else (sequence - previous - 1) % 16
                    writer.writerow([count, f"{count / args.rate:.6f}", sequence, gap,
                                     int(row[5]), f"A{args.channel}", args.rate, received])
                    previous = sequence
                    count += 1
                stream.flush()
        except KeyboardInterrupt:
            print("\nRecording stopped.")
        finally:
            if started:
                try:
                    device.stop()
                except Exception as exc:
                    print(f"Could not stop board: {exc}", file=sys.stderr)
            print(f"Saved {count} samples to {target.resolve()}")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Paired BITalino serial port, for example COM5")
    parser.add_argument("--channel", type=int, choices=range(1, 7), required=True,
                        help="Physical input number: 1 means A1, 2 means A2, etc.")
    parser.add_argument("--rate", type=int, choices=[1, 10, 100, 1000], default=100)
    parser.add_argument("--duration", type=float, help="Seconds of samples; omit to record until Ctrl+C")
    parser.add_argument("--output", default=f"eda_{datetime.now():%Y%m%d_%H%M%S}.csv")
    parser.add_argument("--simulate", action="store_true", help="Generate synthetic data without hardware")
    args = parser.parse_args()
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration must be a finite positive number")
    if args.simulate and args.duration is None:
        parser.error("--simulate requires --duration")
    if not args.simulate and not args.port:
        parser.error("--port is required unless --simulate is used")
    device = None
    try:
        if not args.simulate:
            from bitalino import BITalino
            device = BITalino(args.port, timeout=5)
        record(args, device)
        return 0
    except Exception as exc:
        print(f"Recording failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if device is not None:
            try:
                device.close()
            except Exception as exc:
                print(f"Could not close connection: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
