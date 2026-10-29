# Haptic biofeedback armband — input side (NUS CDE5312, Group 5)

Estimates the wearer's physiological arousal every second, relative to their own baseline, and
sends `state, arousal, valid, t_ms` to the output side (airbag).

- **EDA**: BITalino EDA sensor on A1 → Bluetooth → laptop, 100 Hz
- **Motion**: BITalino ACC sensor on a second input (A5 by default, `--acc-channel`), one axis, 100 Hz
- **PPG**: RT7015 pulse module over its own BLE link → laptop, 500 Hz AOUT + SOUT; beats detected on the laptop
- **ESP32-S3** (optional, `--esp32`): output link only, receives `C,state,arousal,valid,t_ms`

## Common commands

Run in `C:\therapy-haptic-wearable`, after activating the virtual environment.
Our ports: BITalino = COM15, ESP32 = COM11, RT7015 = `70:15:11:69:00:1D`.

```powershell
# Environment
.\.venv\Scripts\Activate.ps1                       # enter the virtual environment (prompt shows (.venv))
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # only if activation is blocked

# Hardware checks (nothing saved)
python -m serial.tools.list_ports -v                         # list ports
python tools\ppg_check.py --address 70:15:11:69:00:1D        # RT7015: samples/s, AOUT/SOUT, beats, HR
python tools\acc_check.py --port COM15 --channel 5           # BITalino ACC readings + calibration (Cmin/Cmax)

# Therapy session (actual use): press 1 chat at the small talk, 5 therapy after it
python tools\run_session.py --subject c01 --eda COM15 --ppg-address 70:15:11:69:00:1D --esp32 COM11 --protocol config\therapy.yaml --plot

# Data collection (see docs/testing_process.md)
python tools\run_session.py --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot
python tools\run_session.py --subject sim --simulate --warmup-s 10 --baseline-s 20 --seconds 90 --plot   # no hardware

# After a session
python tools\replay_session.py sessions\p01_<date_time>              # replay_*.csv + overview .png
python tools\make_labels.py sessions\p01_<date_time>                # features_labeled.csv

# Watch the UDP output (second terminal)
python -c "import socket;s=socket.socket(2,2);s.bind(('127.0.0.1',5005));[print(s.recv(4096).decode()) for _ in iter(int,1)]"
```

Useful `run_session.py` options: `--plot` (live window), `--order` / `--protocol` (steps from
`config/protocol.yaml`), `--esp32 COM11` (serial output), `--ppg none`, `--sinks udp|serial|none`,
`--send-features`, `--seconds N`, `--warmup-s`, `--baseline-s`, `--profile file.json`,
`--confidence hard`, `--no-smoothing`, `--baseline-mode fixed|always|gated`.
`replay_session.py` takes the same processing options (e.g. `--confidence hard`, `--print`).

## Layout

| Path | What |
|---|---|
| `tools/run_session.py` | **Main program**: record, process and output live; `--plot` live window |
| `tools/replay_session.py` | Re-run the processing on a recording, write CSV + overview plot |
| `tools/make_labels.py` | Offline: markers → per-second condition, phase_idx, after, sr, is_baseline |
| `tools/ppg_check.py`, `tools/acc_check.py` | Hardware checks: RT7015 over BLE, BITalino ACC (calibration) |
| `biofeedback/` | Laptop processing package (see Architecture) |
| `biofeedback/settings.py` | **All thresholds, windows and switches in one file** |
| `config/protocol.yaml` | **Test protocol**: steps, durations, orders, condition codes, self-report range |
| `config/therapy.yaml` | Protocol for actual use: settling, opening small talk (baseline), therapy |
| `config/labels.example.yaml` | Optional extra free-text label columns (`make_labels.py --labels`) |
| `docs/testing_process.md` | How to run a test session |
| `firmware/esp32/` | ESP32-S3 firmware (PlatformIO) for the output link |
| `vendor/` | RT7015 manual and the manufacturer's Python example, reference only (not imported) |
| `sessions/` | Recordings (not on GitHub) |

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

In VS Code, select the interpreter once (Ctrl+Shift+P → "Python: Select Interpreter" → `.venv`);
new terminals then activate it automatically. Without activating, use
`.\.venv\Scripts\python.exe` instead of `python`.

### Finding the devices

- BITalino: pair it in Windows Bluetooth first. In `python -m serial.tools.list_ports -v`, use the
  Bluetooth port whose hwid contains the device address (not all zeros).
- RT7015: no pairing needed; close the vendor app. Without `--ppg-address` the program scans for a
  name containing `RT7015`.
- ESP32: `Silicon Labs CP210x USB to UART Bridge`.

### Flashing the ESP32

Connect the board's **UART** USB port, close anything using its COM port, then:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d firmware/esp32 -t upload --upload-port COM11
```

(In VS Code's PlatformIO extension, open the `firmware/esp32` folder.) If it hangs at
`Connecting...`: hold BOOT, press RST, release BOOT, upload again.

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
BITalino (Bluetooth serial) -> EdaSource ----------------> eda, acc
RT7015 (BLE, bleak)         -> PpgBleSource -> PpgStream -> ppg   (500 Hz AOUT/SOUT)
                                                         -> beats (detected on the laptop)
keyboard / live window      -> markers
   every sample -> Recorder (one CSV per stream) + Processor.add (+ LiveWindow with --plot)
   every 1 s   -> Processor.step -> features.csv + output sinks (UDP, ESP32 serial)
ESP32 (USB serial)          <- C,state,arousal,valid,t_ms; what it prints goes to esp32_log
```

- `Processor` (`biofeedback/pipeline.py`) is the same code live, in replay and in simulation;
  only the data source differs. A replay of a session gives exactly the live results.
- Every CSV starts with `t_ms`: laptop time, milliseconds since the session start (`session.json`
  has the start time).

## Processing (every second)

1. **Features** (windows in `settings.py`):
   x1 EDA level (mean µS, 30 s), x2 EDA slope (µS/min, 60 s), x3 SCRs per minute (60 s; rise
   ≥ 0.1 µS within 5 s, counted when it falls back by half), x4 heart rate (median of valid
   beats, 30 s). Beats (vendor logic): SOUT > 1500 mV is high, a rising edge is a candidate,
   accepted if AOUT rises > `PPG_MIN_PULSE_MV` (100 mV) by the falling edge; IBI only between two
   accepted beats with nothing rejected or lost in between. Valid beat: IBI 300–2000 ms, within
   30 % of the median of the last 5 IBIs; no valid beat for 3 s → heart rate missing (z = 0).
   EDA: 100 Hz → 100 ms bins → 0.5 s smoothing, µS = ADC / 2^10 × 3.3 / 0.132 (BITalino EDA
   datasheet Rev B, range 0–25 µS).
2. **Confidence** c = EDA check × motion confidence. EDA check is 0 if the link dropped, a value is
   outside 0.05–24.5 µS or changes faster than 10 µS/s. Motion confidence goes linearly from 1 at
   0.02 g to 0 at 0.10 g of the standard deviation of the ACC over the last 1 s
   (g = (ADC − Cmin) / (Cmax − Cmin) × 2 − 1; Cmin/Cmax from `tools/acc_check.py` into
   `settings.py`); 0 if the BITalino link is lost. valid = c ≥ 0.2. `--confidence hard` makes c 0 or 1.
3. **Baseline**: the first `--warmup-s` (default: settling time of the protocol, 180 s) are
   ignored; mu is the mean of the next `--baseline-s` (default 120 s) seconds with c ≥ 0.8
   (extended if fewer than 90 such seconds). sigma comes from the profile (or defaults), with the
   defaults as a floor.
4. **Score**: z = (x − mu) / sigma, p = sigmoid(w · z + b).
5. **Smoothing**: a ← a + 0.3 · c · (p − a) (`--no-smoothing` to turn off).
6. **State** (0 steady, 1 rise, 2 spike, 3 state_recovery), minimum 5 s per state; spike = EDA rise
   ≥ 0.2 µS within 10 s; rise = a ≥ 0.6 or slope z ≥ 1; after rise or spike → state_recovery until
   a < 0.4 and the slope z ≤ 0.

   **Two different "recovery"s:** `state_recovery` (state 3) is computed every second and means
   *arousal is falling* after a rise or spike. The **condition** `recovery` (marker code `4`) is a
   step of the test protocol: *the participant sits quietly after a task*. They are independent:
   the analysis checks how often the state is `state_recovery` during the `recovery` condition.
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

- **UDP JSON** to `127.0.0.1:5005` (`--udp` to change):
  `{"t_ms": 120000, "state": 1, "arousal": 0.634, "valid": true}`; `--send-features` adds the row.
- **ESP32 serial** (with `--esp32`): `C,state,arousal,valid,t_ms` (e.g. `C,1,0.634,1,120000`).
  The output side must parse it and stop the airbag after 3 s without a line.

## Session files

`sessions/<subject>_<date_time>/` (not in git):

| File | Columns |
|---|---|
| `session.json` | ports, switches, profile, protocol and all settings used |
| `eda.csv` | t_ms, recv_ms, sample_index, sequence, sequence_gap_mod16, eda_raw_adc, eda_us |
| `acc.csv` | t_ms, recv_ms, sample_index, acc_raw_adc, acc_g (same samples as eda.csv) |
| `ppg.csv` | t_ms, aout_mv, sout_mv (every sample; t_ms from sample count / rate, anchored on packet receive times) |
| `beats.csv` | t_ms (rising edge), confirm_ms, ibi_ms, bpm, aout_change_mv (before the validity check; ibi 0 = no previous beat) |
| `esp32_log.csv` | t_ms, line (what the ESP32 prints, with `--esp32`) |
| `markers.csv` | t_ms, local_time, text (condition codes, `sr:N`, `note:...`, `end`) |
| `features.csv` | 1 row per second: phase, x1–x4, eda_rise, motion_rms, c_eda, c_motion, c, valid, z, p, arousal, state, mu |
| `baseline.json` | initial and final mu |

Recordings made before 2026-10-29 used the ESP32 IMU or wired PPG; replaying them now gives no
motion confidence and/or heart rate (a warning is printed).

## Status

| Part | Tested with |
|---|---|
| EDA conversion, BITalino link at 100 Hz | real hardware |
| BITalino ACC (two-channel read, calibration 415 / 668) | real hardware |
| RT7015 over BLE (connect, start command, 500 samples/s, 0 bad frames), beat detection, HR | real hardware (p01, 2026-10-29) |
| Frame parser, sample clock, reconnect handling, replay = live | simulation and unit checks |
| Live window (`--plot`), protocol file | simulation only |
| ESP32 serial `C,...` output | sent; nothing on the ESP32 acts on it yet |
| Training / analysis scripts | not done |
