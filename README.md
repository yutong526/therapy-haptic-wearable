# Haptic biofeedback wearable: waist airbag with breathing guidance (NUS CDE5312, Group 5)

Input: BITalino EDA and accelerometer, RT7015 PPG. Output: an airbag at the waist that inflates and deflates at
6 breaths/min when stress rises. No vibration.

Estimates the wearer's physiological arousal every second, relative to their own baseline, and
sends `state, arousal, valid, t_ms` to the output side (airbag).

- **EDA**: BITalino EDA sensor on A1 → Bluetooth → laptop, 100 Hz
- **Motion**: BITalino ACC sensor on a second input (A5 by default, `--acc-channel`), one axis, 100 Hz
- **PPG**: RT7015 pulse module over its own BLE link → laptop, 500 Hz AOUT + SOUT; beats detected on the laptop
- **ESP32 DevKit + airbag pump** (optional, `--esp32`, `firmware/pump`): gets pump on/off and a
  heartbeat every second (and `C,state,arousal,valid,t_ms` with the `serial` sink)

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
python tools\run_session.py --name <name> --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot
python tools\run_session.py --subject sim --simulate --warmup-s 10 --baseline-s 20 --seconds 90 --plot   # no hardware

# Airbag test at rest, one body part per run (press 0 rest / 6 airbag; the pump follows)
python tools\run_session.py --name <name> --site wrist --protocol config\airbag_test.yaml --eda COM15 --ppg-address 70:15:11:69:00:1D --esp32 COM11 --baseline-source rest --sinks none --plot
python tools\analyze_airbag.py sessions\<date>_<name>_wrist [more runs ...]   # table, airbag_overview.png

# Control test: double-click control_test.bat (name only; same window as the demo). Notes note:math /
# note:english ... note:end mark the tasks; the airbag may start in one of them only (alternates per session)
python tools\analyze_control.py sessions\<date>_<name> [more sessions ...]   # aligned on the (would-be) trigger

# Closed loop (also the demo): pump on / off from the state (settings PUMP_*); config\therapy.yaml by
# default, steps run by themselves; Inflate / Breathe / Deflate / Auto buttons, banner + beep at each pump change
python tools\run_session.py --name demo --eda COM15 --ppg-address 70:15:11:69:00:1D --esp32 COM16 --pump auto --plot

# Demo with one click: double-click demo.bat, type a name (saved to sessions\<date>_<name>).
# config\demo.yaml: 1 min settling + 2.5 min chat baseline, the pump can trigger from about 3.5 min on;
# pump over Bluetooth, COM18; ports at the top of the .bat. Notes: note box in the window or note:<text>

# Screenshot of the live window at any moment of a recording (rule: show what the closed loop decided)
python tools\snapshot_window.py sessions\<folder> <seconds> out.png auto [rule]

# After a session
python tools\replay_session.py sessions\<date>_<name>              # replay_*.csv + overview .png
python tools\make_labels.py sessions\<date>_<name>                # features_labeled.csv
python tools\build_dataset.py sessions\<date>_<name>              # training samples -> results\dataset.csv
python tools\train_person.py results\dataset.csv                  # per-person model, candidate profiles

# Watch the UDP output (second terminal)
python -c "import socket;s=socket.socket(2,2);s.bind(('127.0.0.1',5005));[print(s.recv(4096).decode()) for _ in iter(int,1)]"
```

Useful `run_session.py` options: `--plot` (live window, with a Pause button), `--resume FOLDER` (continue a stopped session), `--order` / `--protocol` (steps from
`config/protocol.yaml` with `--pump protocol`, else `config/therapy.yaml`; a protocol without `self_report`
is not a data collection: no self-report / marker buttons, no ML hints; `--order next`: the order
after the last finished session with that protocol file), `--manual-steps`, `--esp32 COM16` (airbag pump), `--pump protocol|auto|off`, `--site`, `--ppg none`, `--sinks udp|serial|none`,
`--send-features`, `--seconds N`, `--warmup-s`, `--baseline-s`, `--profile file.json`,
`--confidence hard`, `--no-smoothing`, `--baseline-mode fixed|always|gated`.
`replay_session.py` takes the same processing options (e.g. `--confidence hard`, `--print`).

## Layout

| Path | What |
|---|---|
| `tools/run_session.py` | **Main program**: record, process and output live; `--plot` live window |
| `tools/replay_session.py` | Re-run the processing on a recording, write CSV + overview plot |
| `tools/make_labels.py` | Offline: markers → per-second condition, phase_idx, after, sr, is_baseline |
| `tools/build_dataset.py`, `tools/train_person.py` | Training samples, per-person model (`docs/analysis_plan.md`) |
| `tools/analyze_airbag.py` | Airbag test: EDA / HR / motion per block, airbag minus rest, per body part |
| `tools/analyze_control.py` | Control test: airbag block vs control block, aligned on the (would-be) trigger |
| `tools/snapshot_window.py` | Screenshot of the live window at a chosen moment of a recording |
| `demo.bat`, `control_test.bat` | One click: ask for a name, then run the demo / the control test |
| `biofeedback/pump.py` | Closed-loop pump rule (`--pump auto`) |
| `tools/pump_console.py` | Pump by hand: commands, live pressure plot (comfort test, PI tuning) |
| `tools/ppg_check.py`, `tools/acc_check.py` | Hardware checks: RT7015 over BLE, BITalino ACC (calibration) |
| `biofeedback/` | Laptop processing package (see Architecture) |
| `biofeedback/settings.py` | **All thresholds, windows and switches in one file** |
| `config/protocol.yaml` | **Test protocol**: steps, durations, orders, condition codes, self-report range |
| `config/therapy.yaml` | Protocol for actual use: settling, opening small talk (baseline), therapy |
| `config/airbag_test.yaml` | Airbag test: rest / airbag blocks (`pump: true` = pump on during the condition) |
| `config/demo.yaml` | Short demo: 1 min settling, 2.5 min chat baseline, then the closed loop |
| `config/control_test.yaml` | Control test: math / English marked by notes, airbag allowed in one task only |
| `config/labels.example.yaml` | Optional extra free-text label columns (`make_labels.py --labels`) |
| `docs/testing_process.md` | How to run a test session |
| `firmware/pump/` | Airbag pump firmware, classic ESP32 DevKit (Arduino IDE or PlatformIO) |
| `vendor/` | RT7015 manual and the manufacturer's Python example, reference only (not imported) |
| `sessions/` | Recordings; on GitHub only five pseudonymised sessions (A, B demo; C_3, D control test; E data collection) |

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
- ESP32 (pump): over USB, `Silicon Labs CP210x USB to UART Bridge`; or over Bluetooth: pair
  `Airbag-ESP32` in Windows (Bluetooth settings → More Bluetooth options → COM Ports: use the
  **Outgoing** port) and give that port to `--esp32` / `pump_console.py`. USB and Bluetooth work
  at the same time with the same commands. A Bluetooth drop stops the pump after 5 s (heartbeat).
  On this laptop the Bluetooth port is COM18 (hwid ends in the board's MAC + 2, 004B123E5C4E;
  tested 2026-11-04: settings and P lines received, commands answered). Without the USB cable the
  board needs its own power supply.

### Flashing the ESP32

Close anything using its COM port, then either open `firmware/pump/pump.ino` in the Arduino IDE
(board "ESP32 Dev Module") and upload, or:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d firmware/pump -t upload --upload-port COM11
```

If it hangs at `Connecting...`: hold BOOT, press EN, release BOOT, upload again.

**Pressure control**: XGZP6847A (0–100 kPa, 0.5–4.5 V at 5 V, P = (V − 0.5) / 0.04 kPa) on GPIO34.
Breathing (default, `b`) is open loop: after `s`, 4 s inflate pump at `a<pwm>` (default 255, off
early at the inhale pressure `i<kPa>`, 3 kPa) and 6 s deflate pump at `d<pwm>` (default 120, off
early at the exhale pressure `e<kPa>`, −1 kPa), repeated. (A PI controller was too weak here: the
bag on the table reached only 0.7 kPa, 2026-11-04.) Constant pressure (`c<kPa>`): fill, then a PI
controller at 100 Hz drives the inflate pump (output > 0) or the deflate pump (output < 0). `x` stops and deflates (pump 2) until
below `v<kPa>` (default −1 kPa, suction; the sensor is only specified for 0–100 kPa, so readings
below 0 are uncalibrated), below the sensor range or after 20 s. Safety in the firmware: above `MAX_KPA` (placeholder 8 kPa = 60 mmHg), sensor out
of range or fill not reached in 90 s → stop and deflate, start blocked until the next `x`; after
the first heartbeat `h`, 5 s without one stops the pump. Firmware placeholders to check: `MAX_KPA`,
`PWM_MIN` (80, lowest PWM at which a pump turns), Kp 150 and Ki 20.

Find comfortable targets and tune the PI with the console (live plot of pressure vs target):

```powershell
python tools\pump_console.py COM11 --name wrist
# then type: s   c2   c3   c4   b   e1   i3   p80   n30   x   note:<text>   ?```

The targets for sessions are `PUMP_EXHALE_KPA` / `PUMP_INHALE_KPA` in `settings.py` (sent on
connect). The firmware prints `P,<ms>,<kPa>,<target>,<pwm in>,<pwm out>,<phase>` (10 Hz, logged in
`esp32_log.csv`).

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
                                 -> pump on/off (--pump: protocol markers or PumpController) -> pump.csv
ESP32 pump (USB serial)     <- h + s/x every second (+ C lines); what it prints goes to esp32_log
```

- `Processor` (`biofeedback/pipeline.py`) is the same code live, in replay and in simulation;
  only the data source differs. A replay of a session gives exactly the live results.
- Every CSV starts with `t_ms`: laptop time, milliseconds since the session start (`session.json`
  has the start time).

## Processing (every second)

1. **Features** (windows in `settings.py`):
   x1 EDA level (mean µS, 30 s), x2 EDA slope (µS/min, 60 s), x3 SCRs per minute (60 s; rise
   ≥ 0.1 µS within 5 s, counted when it falls back by half), x4 heart rate (median of valid
   beats, 30 s). Beats (`PPG_BEAT_METHOD = "aout"`): peaks of the AOUT pulse wave after removing
   its 1 s centred mean and 50 ms smoothing; a peak counts if it is above half of the largest one
   of the last 2 s and ≥ 330 ms after the previous beat (detected 0.5 s late). `"sout"` = the
   vendor rule on the SOUT square wave. IBI = time between consecutive beats (none across a gap
   in the samples). Valid beat: IBI 300–1333 ms (45–200 bpm), within 30 % of the median of the
   last 5 IBIs; no valid beat for 5 s → heart rate missing (z = 0).
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
   ≥ 0.5 µS within 10 s; rise = a ≥ 0.8 or slope z ≥ 1; after rise or spike → state_recovery until
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
- **ESP32 serial** (with `--esp32`): `C,state,arousal,valid,t_ms` (e.g. `C,1,0.634,1,120000`),
  for information; the pump firmware ignores it.

Airbag pump (`--esp32`, independent of `--sinks`), `--pump`:

- `protocol` (default): on during conditions with `pump: true` in the protocol file (airbag test),
  until `sr:N`, the next code, `pause` or `end`.
- `auto`: closed loop. On after 15 valid seconds of rise / spike (invalid ones neither count nor reset) (once the baseline is
  ready); on at least 60 s; off after 30 valid seconds of steady / state_recovery, or after 300 s
  anyway; no new start for 60 s after a stop. Numbers: `PUMP_*` in `settings.py`.
- Protocol and controller use the breathing assist (4 s inflate / 6 s deflate, `PUMP_*_PWM` and
  `PUMP_*_KPA` in `settings.py`).
- By hand, in any mode (window buttons, or typed like a marker, also logged as one):
  `pump:inflate` (Inflate: fill and hold `PUMP_HOLD_KPA`), `pump:breathe` (Breathe: breathing
  assist, the manual trigger), `pump:off` (Deflate) force the pump and the protocol / controller is
  ignored; `pump:auto` (Auto) hands it back (the pump goes to what they want at that moment).
  Switching between inflate and breathe does not deflate in between. Started with `--pump off`,
  Auto starts the closed loop.
- Control test (`config/control_test.yaml`): the closed loop may start the pump only in the airbag
  task's block (`note:<task>` starts a block); elsewhere it only logs `would_on` / `would_off`.
- Live window: the pump box shows who controls the pump, the rule's progress and a banner with a
  beep at each change (green = automatic, purple = breathing by hand, blue = inflated by hand,
  orange = off); a strip at the bottom of the plots marks when the pump was on, in the same colours.
- Every change goes to `pump.csv` (t_ms, on, mode off / hold / breathe / would_on / would_off,
  reason); what the board did is in `esp32_log.csv` (`F,<ms>,<phase>` lines).

## Session files

`sessions/<date>_<name>[_<site>]/` (not in git):

| File | Columns |
|---|---|
| `session.json` | ports, switches, profile, protocol and all settings used |
| `eda.csv` | t_ms, recv_ms, sample_index, sequence, sequence_gap_mod16, eda_raw_adc, eda_us |
| `acc.csv` | t_ms, recv_ms, sample_index, acc_raw_adc, acc_g (same samples as eda.csv) |
| `ppg.csv` | t_ms, aout_mv, sout_mv (every sample; t_ms from sample count / rate, anchored on packet receive times) |
| `beats.csv` | t_ms (the beat), confirm_ms (when detected), ibi_ms, bpm, amplitude_mv (before the validity check; ibi 0 = no previous beat) |
| `esp32_log.csv` | t_ms, line (what the ESP32 prints, with `--esp32`: `F` phase changes, `P` pressure, `#` info / faults) |
| `pump.csv` | t_ms, on, mode, reason (pump commands; control test: also would_on / would_off) |
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
| Live window (`--plot`), protocol file, demo | real hardware (demo runs 2026-11-04/05) |
| Pump firmware (`firmware/pump`): open-loop breathing, constant-pressure PI, Bluetooth serial | real hardware: flashed, breathing 1.1-2.1 kPa / down to -1 kPa with the bag worn at the waist (B, 2026-11-05) |
| Closed loop `--pump auto` (automatic starts and stops) | real hardware (A, B demo; C, D control test; 2026-11-05) |
| Control test (`control_test.yaml`, `--order next`, `analyze_control.py`) | real hardware (C, D, 2026-11-05) |
| `--pump protocol`, `analyze_airbag.py` | simulation only |
| Training scripts (`build_dataset.py`, `train_person.py`) | one real session (E) |
