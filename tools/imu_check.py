"""Show the raw MPU6050 readings from the ESP32, to check the IMU on its own.

Prints 10 lines per second: accel (g), |a| (should be about 1.0 when still), gyro (deg/s).
Flags all-zero readings (IMU asleep / lost power) and prints the firmware's I2C errors.
Ctrl+C to stop. Examples:
    python tools/imu_check.py --port COM11
    python tools/imu_check.py --port COM11 --reset     # restart the board first (wakes the IMU)
"""

import argparse
import math
import time

import serial

EVERY_N = 10  # R lines arrive at 100 Hz; print every 10th


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM11")
    ap.add_argument("--reset", action="store_true", help="restart the board before reading")
    args = ap.parse_args()

    ser = serial.serial_for_url(args.port, 115200, timeout=0.2, do_not_open=True)
    ser.dtr = ser.rts = False
    ser.open()
    if args.reset:  # DevKitC auto-reset: EN low through RTS
        ser.rts = True
        time.sleep(0.2)
        ser.rts = False
        print("Board restarting...")
    ser.reset_input_buffer()
    ser.write(b"r")
    print("    ax      ay      az     |a|   |     gx       gy       gz")
    n = 0
    try:
        while True:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("PPG ready"):
                ser.write(b"r")  # board restarted in its default mode
            if not line.startswith("R,"):
                if line.startswith(("ERROR", "WARN: expected", "WHO_AM_I", "found", "I2C", "Line", "Gyro", "===")):
                    print(line)
                continue
            parts = line.split(",")
            if len(parts) != 10:
                continue
            n += 1
            if n % EVERY_N:
                continue
            ax, ay, az, gx, gy, gz = (float(p) for p in parts[4:10])
            if any(math.isnan(v) for v in (ax, ay, az)):
                print("  read failed (nan)")
                continue
            mag = math.sqrt(ax * ax + ay * ay + az * az)
            flag = "  <-- ALL ZERO: IMU asleep or lost power, press RST" if ax == ay == az == 0 else ""
            print(f"{ax:7.3f} {ay:7.3f} {az:7.3f} {mag:7.3f}   | {gx:8.2f} {gy:8.2f} {gz:8.2f}{flag}")
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
