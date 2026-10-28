"""Stream one BITalino analog input to CSV (or simulate without hardware)."""

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from copy import copy


EDA_VCC = 3.3  # BITalino operating voltage (EDA sensor datasheet Rev B)
EDA_US_PER_VOLT = 1 / 0.132  # transfer function: EDA(uS) = (ADC / 2^n * VCC) / 0.132, range 0-25 uS


def channel_bits(channel):
    """ADC resolution of a physical input: A1-A4 are 10-bit, A5-A6 are 6-bit."""
    return 10 if channel <= 4 else 6


def adc_to_microsiemens(adc, bits=10):
    return adc / (1 << bits) * EDA_VCC * EDA_US_PER_VOLT


class ConnectionLost(Exception):
    """Transport failure carrying the number of samples already saved."""


def board_call(function, *args):
    try:
        return function(*args)
    except Exception as exc:
        raise ConnectionLost(str(exc)) from exc


def record(args, device=None):
    target = Path(args.output)
    if not target.is_absolute():
        target = Path(__file__).resolve().parent / "Outputs" / target
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    previous = None
    started = False
    # Exclusive creation also handles simultaneous recordings safely.
    original = target
    suffix = 0
    while True:
        try:
            stream = target.open("x", newline="", encoding="utf-8")
            break
        except FileExistsError:
            suffix += 1
            target = original.with_name(f"{original.stem}_{suffix:03d}{original.suffix}")
    with stream:
        writer = csv.writer(stream)
        writer.writerow([
            "sample_index", "nominal_elapsed_seconds", "sequence", "sequence_gap_mod16",
            "eda_raw_adc", "eda_microsiemens", "analog_input", "sampling_rate_hz", "batch_received_utc",
        ])
        bits = channel_bits(args.channel)
        stream.flush()
        try:
            if device is not None:
                board_call(device.start, args.rate, [args.channel - 1])
                started = True
            print(f"Recording A{args.channel} at {args.rate} Hz to {target.resolve()}")
            print("Press Ctrl+C to stop.")
            total = None if args.duration is None else math.ceil(args.duration * args.rate)
            batch_size = max(1, args.rate // 10)
            while total is None or count < total:
                size = batch_size if total is None else min(batch_size, total - count)
                if device is None:
                    rows = [[(count + i) % 16, 0, 0, 0, 0,
                             int(((1 << bits) - 1) * (0.5 + 0.1 * math.sin((count + i) / args.rate)))]
                            for i in range(size)]
                else:
                    rows = board_call(device.read, size)
                received = datetime.now(timezone.utc).isoformat()
                if len(rows) != size:
                    raise RuntimeError(f"Expected {size} samples; received {len(rows)}")
                for row in rows:
                    sequence = int(row[0])
                    gap = 0 if previous is None else (sequence - previous - 1) % 16
                    adc = int(row[5])
                    us = adc_to_microsiemens(adc, bits)
                    writer.writerow([count, f"{count / args.rate:.6f}", sequence, gap,
                                     adc, f"{us:.4f}", f"A{args.channel}", args.rate, received])
                    print(f"{count / args.rate:9.3f} s | A{args.channel} | EDA raw ADC: {adc} | {us:.3f} uS")
                    previous = sequence
                    count += 1
                stream.flush()
        except ConnectionLost as exc:
            exc.saved_count = count
            raise
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
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "Outputs" / f"eda_{datetime.now():%Y%m%d_%H%M%S}.csv"))
    parser.add_argument("--retry-delay", type=float, default=3, help="Seconds between reconnection attempts (default: 3)")
    parser.add_argument("--simulate", action="store_true", help="Generate synthetic data without hardware")
    args = parser.parse_args()
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration must be a finite positive number")
    if args.simulate and args.duration is None:
        parser.error("--simulate requires --duration")
    if not args.simulate and not args.port:
        parser.error("--port is required unless --simulate is used")
    if not math.isfinite(args.retry_delay) or args.retry_delay <= 0:
        parser.error("--retry-delay must be a finite positive number")
    try:
        if args.simulate:
            record(args)
            return 0
        from bitalino import BITalino
        return run_live(args, BITalino)
    except KeyboardInterrupt:
        print("\nRecording stopped.")
        return 0
    except Exception as exc:
        print(f"Recording failed: {exc}", file=sys.stderr)
        return 1


def run_live(args, factory):
    remaining = None if args.duration is None else math.ceil(args.duration * args.rate)
    while remaining is None or remaining > 0:
        device = None
        try:
            print(f"Connecting to {args.port}...")
            device = board_call(factory, args.port, 5)
            segment = copy(args)
            # Avoid ceil adding an extra sample due to floating-point rounding.
            segment.duration = None if remaining is None else math.nextafter(remaining / args.rate, 0)
            record(segment, device)
            return 0
        except ConnectionLost as exc:
            if remaining is not None:
                remaining -= getattr(exc, "saved_count", 0)
            print(f"Connection interrupted: {exc}. Saved data is preserved.", file=sys.stderr)
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception as exc:
                    print(f"Could not close connection: {exc}", file=sys.stderr)
        print(f"Retrying in {args.retry_delay:g} seconds; Ctrl+C to stop.")
        time.sleep(args.retry_delay)



if __name__ == "__main__":
    sys.exit(main())
