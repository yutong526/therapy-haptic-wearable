# System brief for an AI assistant: input side and input → output link

Give this file to an AI assistant before asking it about this project. It describes the hardware,
the software and the numbers as they are in the code on 2026-11-06. File paths are relative to
the repository root. When this file and the code disagree, the code (`biofeedback/settings.py`,
`firmware/pump/pump.ino`) is right.

## 1. What the system does

A wearable biofeedback system for therapy sessions (NUS CDE5312). A laptop reads skin conductance
(EDA), a motion signal and a pulse wave (PPG) from the user, turns them into a stress estimate once
per second, and, when stress rises, an airbag **at the waist** starts a slow breathing rhythm
(inflate 4 s, deflate 6 s = 6 breaths/min) for the user to breathe along with. The airbag is at the
waist, the sensors are elsewhere (EDA electrodes and ACC with the BITalino, PPG module separate),
so the bag does not press on the electrodes.

```
INPUT (sensors)                     LAPTOP (Python, tools/run_session.py)              OUTPUT
BITalino EDA (A1) ─┐ BT serial      EdaSource ─┐                                       ESP32 DevKit
BITalino ACC (A5) ─┘ COM15          PpgBleSource ─┼─> Processor.step() every 1 s ──>   (USB COM16 or
RT7015 PPG ── BLE ─────────────────>             │    features -> z -> arousal ->      BT COM18)
                                                 │    state                            two pumps + pressure
                                    markers ─────┘    PumpController (closed loop) ─>  sensor -> airbag
                                    (window / terminal)                Esp32Source
```

## 2. Input hardware

| Device | Link | What it gives |
|---|---|---|
| BITalino (r)evolution board, EDA sensor on input A1 | Bluetooth classic serial, COM15 on this laptop (MAC 98:D3:11:FD:F9:C1; an older board is COM13) | EDA at 100 Hz, 10-bit ADC |
| BITalino ACC sensor on input A5 (one axis) | same link, same samples | motion, 100 Hz |
| RT7015 PPG module | Bluetooth Low Energy, address 70:15:11:69:00:1D, no pairing; close the vendor app first | pulse waveform AOUT and a pulse square wave SOUT at 500 samples/s, 20 samples per notification |

Conversions (in `biofeedback/eda.py`):
- EDA: `uS = adc / 1024 * 3.3 / 0.132` (BITalino EDA sensor Rev B datasheet). 1 ADC count ≈ 0.024 uS.
- ACC: `g = (adc - 415) / (668 - 415) * 2 - 1` (calibrated: 415 = -1 g, 668 = +1 g).

RT7015 over BLE (`biofeedback/ppg_ble.py`): notify UUID `0000FFF4-...`, write UUID
`0000FFF3-...`, start command `AA 04 80 11 01 2F BB`. Frames are `A5 len addr check payload 5A`;
the parser drops broken frames and re-joins frames split over two notifications. A sample clock
places each sample in time (sample count / rate, anchored on packet arrival, slow drift correction).

## 3. Input software, per stage

All thresholds, windows and switches are in `biofeedback/settings.py` (one file on purpose).

1. **Recording.** Every raw sample goes to a CSV in `sessions/<date>_<name>/` (`eda.csv`,
   `acc.csv`, `ppg.csv`, `beats.csv`). Nothing is discarded while recording.
2. **Beat detection** (laptop, on the AOUT waveform): smoothed 50 ms, peak = local maximum above
   half of the recent range, refractory 330 ms, minimum pulse 100 mV. A beat is valid if its
   inter-beat interval (IBI) is 300-2000 ms and within 30 % of the median of the last 5 IBIs.
3. **EDA cleaning:** 100 Hz averaged into 10 Hz bins, 0.5 s moving average.
4. **Features, once per second** (`biofeedback/features.py`):
   - `eda_level`: mean EDA over 30 s (uS)
   - `eda_slope`: linear slope over 60 s (uS/min)
   - `scr_rate`: skin conductance responses per minute over 60 s (rise ≥ 0.1 uS within 5 s)
   - `hr`: 60000 / median valid IBI over 30 s; missing if no valid beat for 5 s
   - `eda_rise`: EDA rise over the last 10 s (only for the "spike" state)
5. **Confidence c = c_eda × c_motion** (`biofeedback/pipeline.py`):
   - `c_eda` is 0 if the EDA is below 0.05 or above 24.5 uS, changes faster than 10 uS/s, or the
     link dropped; else 1.
   - `c_motion` comes from the standard deviation of the ACC over the last second: 1 at ≤ 0.02 g,
     0 at ≥ 0.10 g, linear in between; 0 if the link dropped.
   - A second is **valid** if c ≥ 0.2.
6. **Baseline μ** (the user's calm level): taken from the "chat" phase at the start (first 60 s
   dropped, at least 60 valid seconds with c ≥ 0.8). Before it is ready, phase = warmup /
   baseline and nothing is triggered. Afterwards phase = "run" and μ is updated slowly
   (β = 1/600 per second) only while the state is calm ("gated").
7. **z-scores:** `z = (x - μ) / σ`, clipped to ±5. σ comes from a per-person profile
   (`profiles/<subject>.json`) or the defaults, which are also the floor: eda_level 0.3 uS,
   eda_slope 0.5 uS/min, scr_rate 2 /min, hr 5 bpm. A missing feature counts as z = 0.
8. **Score and arousal:** `p = sigmoid(w · z + b)` with default weights eda_level 0.5,
   eda_slope 1.0, scr_rate 1.0, hr 0.5 and bias -1.0. These are **set by hand, not learned**
   (see section 8); a profile file could replace them. Then
   `arousal ← arousal + 0.3 · c · (p - arousal)`: low-confidence seconds barely move it.
9. **State** (each state lasts at least 5 s):
   - **spike (2):** EDA rose ≥ 0.5 uS within 10 s
   - **rise (1):** arousal ≥ 0.8, or z of eda_slope ≥ 1.0
   - **state_recovery (3):** after rise / spike, until arousal < 0.4 and the slope z ≤ 0
   - **steady (0):** otherwise

Every second, one row is written to `features.csv` (phase, features, c, valid, z, p, arousal,
state, μ) and sent to the outputs.

## 4. Input → output: how the pump is decided

`tools/run_session.py --pump auto` uses `PumpController` (`biofeedback/pump.py`) on each
features row:

- **Start:** the baseline is ready (phase "run") and there have been **15 valid seconds of rise or
  spike**. Invalid seconds neither count nor reset the count; any other valid state resets it. No
  start within 60 s of the last stop.
- **Stop:** after at least 60 s on, once there have been **30 valid seconds of steady or
  state_recovery**; or after 300 s on in any case.
- The decision is "breathing assist on / off". Other modes:
  - `--pump protocol`: on during protocol conditions marked `pump: true`.
  - `--pump off`: buttons only.
  - By hand, in any mode, from the window buttons or typed: `pump:inflate`, `pump:breathe`,
    `pump:off`, and `pump:auto` to hand control back.
- **Control test** (`config/control_test.yaml`): the rule may start the pump only in the block of
  this session's airbag task. Blocks start with the note `note:math` / `note:english`. Elsewhere
  it only writes `would_on` / `would_off` to `pump.csv`.

Other outputs, once per second: UDP JSON to `127.0.0.1:5005`, e.g.
`{"t_ms": 120000, "state": 1, "arousal": 0.634, "valid": true}`; and a line `C,state,arousal,valid,t_ms`
on the ESP32 link, which the pump firmware ignores.

## 5. The link to the ESP32 (`biofeedback/esp32.py`)

- Serial text, 115200 baud, over USB (CP210x, COM16) or Bluetooth SPP (device "Airbag-ESP32",
  outgoing port COM18). Both work at the same time.
- On connect, the laptop sends the breathing settings as line commands:
  `e-1.00`, `i3.00`, `a255`, `d120`, `b`, each followed by a newline.
- To start, the laptop sends a mode line, then `s`:
  - breathing: `b` + newline, then `s`
  - constant pressure: `c0.60` + newline, then `s`
- `x` stops the pump and deflates it.
- **Heartbeat:** every 1 s the laptop sends `h` plus `s` or `x` (the wanted state). The firmware
  ignores repeats. Without `h` for 5 s it stops and deflates, so a lost link or a crashed laptop
  stops the pump.
- When the board restarts (its line starting `# ESP32 airbag pump` is seen), the laptop resends
  the settings.
- The board prints:
  - `F,<ms>,<phase>` at each phase change;
  - `P,<ms>,<kPa>,<target>,<pwm in>,<pwm out>,<phase>` 10 times a second (once a second when IDLE);
  - `# ...` lines with info and faults.

  These lines are logged to `esp32_log.csv`. Garbage bytes seen on the USB link are filtered out
  by `clean_line`.

## 6. Output hardware and firmware, for context (`firmware/pump/pump.ino`)

**Hardware**
- Classic ESP32 DevKit (PlatformIO env `esp32dev`, Arduino core).
- Dual H-bridge: pump 1 (PWMA 25 / AIN1 27 / AIN2 26) inflates, pump 2 (PWMB 18 / BIN1 19 / BIN2 21) deflates, STBY 22. There is no valve.
- Pressure sensor XGZP6847A (0-100 kPa gauge, 0.5-4.5 V) on GPIO34 through a 12k/20k divider.
  `kPa = (V - 0.5) / 0.04`; zeroed at boot (about 0.6 kPa offset).

**Breathing (default), open loop**
- INHALE 4 s: pump 1 at PWM 255, switched off early once the pressure reaches 3 kPa.
- EXHALE 6 s: pump 2 at PWM 120, switched off early once the pressure drops below -1 kPa (suction).
- No fill phase.
- Measured with the bag worn at the waist: about 1.1-2.1 kPa at the top and down to -1 kPa.

**Constant pressure (`c`)**
- Fill, then hold the target with a PI controller at 100 Hz (Kp 150, Ki 20, deadband 0.03 kPa).

**Safety**
- Above 8 kPa, the sensor out of range (outside 0.3-4.7 V), or the fill not reached in 90 s: the
  board stops and deflates, and a new `s` is refused until `x`.
- DEFLATE ends below -1 kPa, or after 20 s.

## 7. Running it

```powershell
cd C:\therapy-haptic-wearable
.venv\Scripts\activate
python tools\run_session.py --name <name> --protocol config\demo.yaml --eda COM15 --ppg-address 70:15:11:69:00:1D --esp32 COM18 --pump auto --plot
python tools\run_session.py --name test --simulate --pump auto --plot      # no hardware: simulated BITalino, RT7015, ESP32
```

- **Shortcuts:** `demo.bat` and `control_test.bat` ask only for a name.
- **Protocols** (`config/*.yaml`):
  - `protocol.yaml`: data collection for machine learning;
  - `airbag_test.yaml`: the airbag's effect at rest;
  - `demo.yaml`: short demo (1 min settling + 2.5 min chat baseline);
  - `therapy.yaml`: actual use;
  - `control_test.yaml`: airbag in one task only.
- **Live window (`--plot`):** EDA, motion, PPG with beats, HR, arousal / c on the left. Right panel:
  pump box (Inflate / Breathe / Deflate / Auto, board pressure, rule progress, a banner with a beep
  at each change), step countdown, note box, sensor lights.
- **Analysis:** `tools/replay_session.py` (re-run the processing on a recording),
  `tools/analyze_airbag.py`, `tools/analyze_control.py`.

## 8. Machine learning: planned, not finished

The original plan was to **learn** the weights w, the bias b and σ instead of setting them by hand:
first a population model, then a per-person calibration from one data-collection session
(`docs/analysis_plan.md`). There was not enough time to finish it.

- **Done:**
  - the data-collection protocol `config/protocol.yaml`: chat, rest, math, game and recovery,
    with a self-report 1-5 after each step, and two counterbalanced orders;
  - recordings of a few people (E, p00, p01);
  - offline labelling (`tools/make_labels.py`);
  - the training-sample builder and per-person training script (`tools/build_dataset.py`,
    `tools/train_person.py`), tried on one session (E);
  - candidate profiles for p01 only (`profiles/`, local, not validated).
- **Not done:**
  - enough participants;
  - the population model;
  - validated per-person profiles;
  - an evaluation of the learned model.
- **What the live system really uses:**
  - hand-set default weights, bias and σ (`DEFAULT_WEIGHTS`, `DEFAULT_BIAS`, `DEFAULT_SIGMA` in
    `settings.py`);
  - the rule-based state machine, with thresholds tuned by hand on E's data.
  - The personalisation that is live is the baseline μ, measured in every session, together with
    the per-second confidence c.
- **For reports and slides:** present the learned model as planned / future work, not as a
  result. The processing chain is built so that a learned profile can be dropped in later
  without code changes.

## 9. Known issues (2026-11-06)

- All thresholds marked `(placeholder)` in `settings.py` are tuned on very few people.
  - The default σ for eda_level (0.3 uS) is small for people with large EDA drifts.
  - If the EDA does not return to the baseline after a task (B), arousal stays high and the
    pump runs during rest too.
- Electrodes need about 3 min to settle. With 1 min, the baseline EDA came out too low.
- If the BITalino battery dies, there are no EDA data and the arousal stays where it was. The pump
  then stays on until the 300 s maximum (D). Stopping after N invalid seconds is not implemented yet.
- In some sessions the EDA rose strongly right after the pump started (B, D), in others
  not (A, C). The airbag is at the waist, away from the electrodes, so pressure on the
  electrodes is unlikely; a reaction to the inflation itself (startle / orienting response) is
  possible. `config/airbag_test.yaml` (airbag at rest) measures this.
- The XGZP6847A is specified for 0-100 kPa only, so readings below 0 are uncalibrated.
- `BluetoothSerial` on the ESP32 is deprecated in Arduino core 4.0.
- Whether the airbag helps is not shown yet: the control test has few sessions.

## 10. Rules when changing this project

- Put every threshold or switch in `biofeedback/settings.py`, not in the code.
- Code, comments, file names and commands in English; match the style of the surrounding code.
- Live and replay use the same `Processor`. Do not add live-only processing: a replay of a
  recording must give the same `features.csv`.
- Say what was tested on real hardware and what only in simulation (`--simulate`).
- Do not flash the firmware or run the pump without the user's go-ahead; the user runs the
  hardware tests.
- Commit only when asked. No co-author lines in commits.
