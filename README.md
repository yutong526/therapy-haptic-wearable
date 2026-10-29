# Haptic biofeedback armband — input side (NUS CDE5312, Group 5)

Estimates the wearer's physiological arousal every second, relative to their own baseline, from
three sensors, and sends `state, arousal, valid, t_ms` to the output side (airbag).

- **EDA**: BITalino EDA sensor on A1 → Bluetooth → laptop, 100 Hz
- **PPG**: RT7015 pulse module over its own BLE link → laptop (`--ppg ble`, default, 500 Hz AOUT + SOUT);
  beats detected on the laptop. `--ppg esp32` = old wiring to the ESP32 (firmware B lines)
- **ESP32-S3**: IMU (if not using the BITalino ACC) and the output link
- **Motion**: BITalino ACC sensor on a second BITalino input (`--acc-channel`, one axis, 100 Hz),
  or the GY-521 (MPU6050) on the ESP32, I2C (SDA GPIO8, SCL GPIO9), when `--acc-channel` is not given

## Common commands

Run in `C:\therapy-haptic-wearable`, after activating the virtual environment.
Our ports: ESP32 = COM11, BITalino = COM13 (check with the "list ports" command).

```powershell
# Environment
.\.venv\Scripts\Activate.ps1                       # enter the virtual environment (prompt shows (.venv))
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # only if activation is blocked
deactivate                                         # leave it

# Hardware
python -m serial.tools.list_ports -v               # list ports
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -t upload --upload-port COM11   # flash ESP32
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device monitor --port COM11         # firmware output (Ctrl+C)
python tools\imu_check.py --port COM11 --reset     # raw ESP32 IMU readings (|a| ~ 1.0 when still)
python tools\ppg_check.py                          # RT7015 over BLE: samples/s, AOUT/SOUT, beats (nothing saved)
python tools\acc_check.py --port COM15 --channel 5  # raw BITalino ACC readings + calibration (Cmin/Cmax)

# Recording (type marker + Enter while running; Ctrl+C to stop)
python tools\run_session.py --subject sim --simulate --warmup-s 10 --baseline-s 20 --seconds 90   # no hardware
python tools\run_session.py --subject t_esp --esp32 COM11 --ppg none --warmup-s 10 --baseline-s 20 --seconds 60  # ESP32 only
python tools\run_session.py --subject t_ppg --warmup-s 10 --baseline-s 20 --seconds 60                  # BLE PPG only
python tools\run_session.py --subject t_eda --eda COM13 --warmup-s 10 --baseline-s 20 --seconds 60    # EDA only
python tools\run_session.py --subject test01 --eda COM13 --esp32 COM11 --warmup-s 30 --baseline-s 60 # short test
python tools\run_session.py --subject p01 --eda COM13 --esp32 COM11                                  # study (3 + 2 min baseline)
python tools\run_session.py --subject demo --eda COM13 --esp32 COM11 --esp32-mode live --warmup-s 30 --baseline-s 60  # demo
# Motion from the BITalino ACC instead of the ESP32 IMU: add --acc-channel <input of the ACC>
python tools\run_session.py --subject p01 --eda COM15 --acc-channel 5 --esp32 COM11                 # study, BITalino ACC

# Replay a recording (writes replay_*.csv and .png into the session folder)
python tools\replay_session.py sessions\test01_<date_time>
python tools\replay_session.py sessions\test01_<date_time> --print                          # with status lines
python tools\replay_session.py sessions\test01_<date_time> --confidence hard                 # E3: hard valid flag
python tools\replay_session.py sessions\test01_<date_time> --baseline-mode fixed             # E4: fixed / always / gated

# Labels
python tools\make_labels.py sessions\test01_<date_time> --labels config\labels.example.yaml

# Watch the UDP output (second terminal)
python -c "import socket;s=socket.socket(2,2);s.bind(('127.0.0.1',5005));[print(s.recv(4096).decode()) for _ in iter(int,1)]"
```

Useful `run_session.py` options: `--sinks udp` (no serial output), `--sinks none`,
`--send-features`, `--seconds N`, `--profile file.json`, `--confidence hard`, `--no-smoothing`,
`--baseline-mode fixed|always|gated`.

## Layout

| Path | What |
|---|---|
| `src/main.cpp`, `platformio.ini` | ESP32-S3 firmware: IMU + PPG at 100 Hz, beat detection on the board (PPG part unused with `--ppg ble`) |
| `biofeedback/ppg_ble.py` | RT7015 BLE source: frame parser, sample clock, beat detection |
| `vendor/rt7015_python/` | Manufacturer's example code, reference only (not imported) |
| `biofeedback/` | Laptop processing (see Architecture) |
| `biofeedback/settings.py` | **All thresholds, windows and switches in one file** |
| `tools/run_session.py` | Main program: record all sensors and output state/arousal live |
| `tools/replay_session.py` | Re-run the processing on a recording, write CSV + overview plot |
| `tools/imu_check.py` | Show raw ESP32 IMU readings only (hardware check) |
| `tools/acc_check.py` | Show raw BITalino ACC readings, measure its calibration |
| `tools/ppg_check.py` | Connect to the RT7015 over BLE only and show samples and beats |
| `tools/make_labels.py` | Offline: markers + `labels.yaml` → per-second labels |
| `tools/live_plot.py`, `tools/serial_logger.py` | Older debug tools (live plot, raw ESP32 log) |
| `Input/record_eda.py` | Stand-alone BITalino EDA recorder |
| `config/labels.example.yaml` | Example labelling scheme |

## Setup (once)

```powershell
cd C:\therapy-haptic-wearable
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install --no-deps bitalino==1.2.6
```

The `--no-deps` install skips PyBluez, which needs a C++ compiler; the BITalino is used through
its Bluetooth COM port instead.

### Entering the virtual environment

Every new terminal:

```powershell
.\.venv\Scripts\Activate.ps1
```

The prompt then starts with `(.venv)` and `python` means the project's Python.
If PowerShell says running scripts is disabled, allow it for this window and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

In VS Code, select the interpreter once (Ctrl+Shift+P → "Python: Select Interpreter" → `.venv`);
new terminals then activate it automatically. Without activating, use
`.\.venv\Scripts\python.exe` instead of `python` in the commands below.

### Flashing the ESP32

Connect the board's **UART** USB port, close anything using its COM port, then click
PlatformIO's Upload (→) button in VS Code, or:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -t upload --upload-port COM11
```

If it hangs at `Connecting...`: hold BOOT, press RST, release BOOT, upload again.

### Finding the COM ports

```powershell
python -m serial.tools.list_ports -v
```

- ESP32: `Silicon Labs CP210x USB to UART Bridge` (on our laptop: COM11)
- BITalino: pair it in Windows Bluetooth first. Use the Bluetooth port whose hwid contains the
  device address (not all zeros) — the outgoing port (on our laptop: COM13).

## Testing step by step

1. **Without hardware** (checks the software):
   ```powershell
   python tools\run_session.py --subject sim --simulate --warmup-s 10 --baseline-s 20 --seconds 90
   ```
2. **IMU only**: should show |a| ≈ 1.0 g when still; all zeros = IMU asleep or lost power.
   ```powershell
   python tools\imu_check.py --port COM11 --reset
   ```
3. **ESP32 only / EDA only** (c stays 0 here because the other sensor is missing — look at the
   `eda` / `motion` parts of c instead):
   ```powershell
   python tools\run_session.py --subject t_esp --esp32 COM11 --warmup-s 10 --baseline-s 20 --seconds 60
   python tools\run_session.py --subject t_eda --eda COM13 --warmup-s 10 --baseline-s 20 --seconds 60
   ```
4. **All three** (~8 min). Type a marker + Enter at the start of each step:

   | Step | Marker |
   |---|---|
   | Sit still 1.5 min | `baseline` |
   | Shake the arm 15 s, then stop | `move`, then `still` |
   | Mental arithmetic 1 min (1000 minus 7, repeatedly) | `math start` |
   | Sudden deep breath or a clap behind you | `startle` |
   | Slow breathing 2 min (in 4 s, out 6 s) | `breath` |
   | Finish, then Ctrl+C | `end` |

   ```powershell
   python tools\run_session.py --subject test01 --eda COM13 --esp32 COM11 --warmup-s 30 --baseline-s 60
   ```
5. **Replay and plot**:
   ```powershell
   python tools\replay_session.py sessions\test01_<date_time>
   ```
   Opens nothing; writes `replay_<switches>.csv` and `.png` into the session folder.

Before starting: EDA electrodes on two fingertips (not the palm), wait ~1 min for the reading to
settle; press RST on the ESP32 if the IMU was showing zeros.

### Reading the status line

```
   120 s | run      | EDA 2.85 uS | HR 78 | SCR/min 3.0 | c 0.92 (eda 1 x motion 0.92) | steady 0.21
```

| Field | Meaning |
|---|---|
| phase | `warmup` (ignored), `baseline` (measuring mu), `run` (scoring) |
| EDA | mean skin conductance over 30 s (µS); `-` = not enough data yet |
| HR | median of the valid beats over 30 s (bpm) |
| SCR/min | skin conductance responses (small EDA peaks) per minute over 60 s |
| c | confidence 0–1 = EDA check (0/1) × motion confidence; `(invalid)` when c < 0.2 |
| last part | state and smoothed arousal |

## Architecture

```
BITalino (Bluetooth serial) -> EdaSource ----------------> eda
                                                         -> acc        (with --acc-channel)
RT7015 (BLE, bleak)         -> PpgBleSource -> PpgStream -> ppg        (500 Hz AOUT/SOUT, with --ppg ble)
                                                         -> beats      (detected on the laptop)
ESP32 (USB serial)          -> Esp32Source -> parse_line -> imu        (R lines, record mode)
                                                         -> ppg, beats (only with --ppg esp32)
                                                         -> motion     (S lines, kept in esp32_log)
   motion confidence uses acc if --acc-channel is given, else the S lines (session.json: motion_source)
keyboard                    -> markers
   every sample -> Recorder (one CSV per stream) + Processor.add
   every 1 s   -> Processor.step -> features.csv + output sinks (ESP32 serial, UDP)
```

- `Processor` (`biofeedback/pipeline.py`) is the same code live, in replay and in simulation;
  only the data source differs. A replay of a session gives exactly the live results.
- Sources only know the shared `Clock` and a sink function. The ESP32 transport only needs
  `open/readline/write/close`, so BLE can replace USB serial in one place.
- Every CSV starts with `t_ms`: laptop receive time, milliseconds since the session start
  (`session.json` has the start time). ESP32 rows also keep `board_ms`.
- The firmware is unchanged. ESP32 modes: `--esp32-mode record` (default) also streams 100 Hz raw
  PPG/IMU for offline work; `live` sends only what the processing needs (beats + 1 s summaries).

## Processing (every second)

1. **Features** (windows in `settings.py`):
   x1 EDA level (mean µS, 30 s), x2 EDA slope (µS/min, 60 s), x3 SCRs per minute (60 s; rise
   ≥ 0.1 µS within 5 s, counted when it falls back by half), x4 heart rate (median of valid
   beats, 30 s). Beats (`--ppg ble`, vendor logic): SOUT > 1500 mV is high, a rising edge is a
   candidate, accepted if AOUT rises > 500 mV by the falling edge; IBI only between two accepted
   beats with nothing rejected or lost in between. Valid beat: IBI 300–2000 ms, within 30 % of the median of the last 5 IBIs; no
   valid beat for 3 s → heart rate missing (z = 0). EDA: 100 Hz → 100 ms bins → 0.5 s smoothing,
   µS = ADC / 2^10 × 3.3 / 0.132 (BITalino EDA datasheet Rev B, range 0–25 µS).
2. **Confidence** c = EDA check × motion confidence. EDA check is 0 if the link dropped, a value is
   outside 0.05–24.5 µS or changes faster than 10 µS/s. Motion confidence goes linearly from 1 at
   0.02 g to 0 at 0.10 g of the motion measure: BITalino ACC = standard deviation of the last 1 s
   (g = (ADC − Cmin) / (Cmax − Cmin) × 2 − 1, ACC datasheet; Cmin/Cmax from `tools/acc_check.py`
   into `settings.py`), ESP32 IMU = 1 s RMS of |a| − 1 g. 0 if that link or sensor is lost.
   valid = c ≥ 0.2. `--confidence hard` makes c 0 or 1.
3. **Baseline**: the first `--warmup-s` (default 180 s) are ignored; mu is the mean of the next
   `--baseline-s` (default 120 s) seconds with c ≥ 0.8 (extended if fewer than 90 such seconds).
   sigma comes from the profile (or defaults), with the defaults as a floor.
4. **Score**: z = (x − mu) / sigma, p = sigmoid(w · z + b).
5. **Smoothing**: a ← a + 0.3 · c · (p − a) (`--no-smoothing` to turn off).
6. **State** (0 steady, 1 rise, 2 spike, 3 recovery), minimum 5 s per state; spike = EDA rise
   ≥ 0.2 µS within 10 s; rise = a ≥ 0.6 or slope z ≥ 1.
7. **Baseline update** (`--baseline-mode`): `gated` (default) moves mu by 1/600 per second only
   when steady and c ≥ 0.8, and at a tenth of that rate after 10 min frozen; `always`; `fixed`.

Thresholds marked (placeholder) in `settings.py` are not tuned on real data yet.

## Profiles

`profiles/<subject>.json`, 14 numbers: 4 weights, bias, 4 sigmas, lag; plus 4 reference means.
Without a file the defaults are used. mu is measured at the start of every session.

```json
{
  "weights": {"eda_level": 0.5, "eda_slope": 1.0, "scr_rate": 1.0, "hr": 0.5},
  "bias": -1.0,
  "sigma":   {"eda_level": 0.3, "eda_slope": 0.5, "scr_rate": 2.0, "hr": 5.0},
  "lag_s": 0,
  "mu_reference": {"eda_level": null, "eda_slope": null, "scr_rate": null, "hr": null}
}
```

## Output

Once per second, to every sink in `--sinks` (default `serial,udp`):

- **ESP32 serial**: `C,state,arousal,valid,t_ms` (e.g. `C,1,0.634,1,120000`). The firmware does
  not act on it yet; the output side must parse it and stop the airbag after 3 s without a line.
- **UDP JSON** to `127.0.0.1:5005` (`--udp` to change):
  `{"t_ms": 120000, "state": 1, "arousal": 0.634, "valid": true}`; `--send-features` adds the row.

## Session files

`sessions/<subject>_<date_time>/` (not in git):

| File | Columns |
|---|---|
| `session.json` | ports, switches, profile and all settings used |
| `eda.csv` | t_ms, recv_ms, sample_index, sequence, sequence_gap_mod16, eda_raw_adc, eda_us |
| `acc.csv` | t_ms, recv_ms, sample_index, acc_raw_adc, acc_g (with `--acc-channel`; same samples as eda.csv) |
| `ppg.csv` | `--ppg ble`: t_ms, aout_mv, sout_mv (every sample; t_ms from sample count / rate, anchored on packet receive times) · `--ppg esp32`: t_ms, board_ms, ppg_mv, sout_mv (record mode) |
| `imu.csv` | t_ms, board_ms, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps (record mode) |
| `beats.csv` | `--ppg ble`: t_ms (rising edge), confirm_ms, ibi_ms, bpm, aout_change_mv · `--ppg esp32`: t_ms, board_ms, ibi_ms, bpm (both before the validity check; ibi 0 = no previous beat) |
| `esp32_log.csv` | t_ms, line (S motion summaries, E, P, WARN, ERROR, ...) |
| `markers.csv` | t_ms, local_time, text |
| `features.csv` | 1 row per second: phase, x1–x4, eda_rise, motion_rms, c_eda, c_motion, c, valid, z, p, arousal, state, mu |
| `baseline.json` | initial and final mu |

## Labels (offline)

```powershell
python tools\make_labels.py sessions\test01_<date_time> --labels config\labels.example.yaml
```

Writes `features_labeled.csv` with one column per scheme in the YAML (example: `arousal` 0/1 from
the protocol markers with 60 s dropped after each change, and `self_report` from `score N`).

## Status

| Part | Tested with |
|---|---|
| EDA conversion, BITalino link at 100 Hz | real hardware |
| ESP32 link, beats / heart rate, motion summaries | real hardware |
| IMU | real hardware: works after reset, but **drops out** (reads all zeros) after some minutes — wiring/power to be fixed |
| Full live loop with all three sensors | not yet completed on hardware (IMU dropout, then BITalino not connected) |
| Processing, replay = live, plot, UDP output | simulation |
| BITalino ACC as motion source (two-channel read, acc.csv, replay = live) | simulation; ACC calibration measured on hardware (415 / 668) |
| RT7015 over BLE (scan/connect, start/stop commands, notifications) | **not yet tested on hardware** |
| RT7015 frame parser, sample clock, beat detection, reconnect handling, replay from ppg.csv = live | simulation and unit checks only |
| Serial `C,...` output | sent, but nothing on the ESP32 uses it yet |
| Self-report filter for labels, training code | not done |
