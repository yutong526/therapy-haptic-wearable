# Testing process

The steps, durations, order and condition codes are defined in **`config/protocol.yaml`**; the
program reads that file. To change the protocol, edit the file (no code change). The table below
is the current content; each session saves a copy of the protocol it used in `session.json`.

One session takes about **26 min** plus the self-reports: 3 min settling, then six steps.

## Steps and markers

At the **start** of each step, enter its code (button in the live window, or type it + Enter in
the terminal). After each step, enter the participant's self-report `sr:1` ... `sr:5`
(1 = calm ... 5 = very stressed), **then** start the next step.

Order `math_first` (odd participant numbers, e.g. p01, p03):

| Step | Activity | Duration | Code |
|---|---|---|---|
| — | Electrode settling: sit quietly (not used) | 3 min | — (automatic) |
| 1 | Neutral chat (how you came today, breakfast); sensor hand still | 4 min | `1` chat |
| 2 | Silent rest: no talking, sensor hand on the table | 3 min | `0` rest |
| 3 | Mental arithmetic, timed and scored; sensor hand still | 5 min | `2` math |
| 4 | Recovery: sit quietly | 3 min | `4` recovery |
| 5 | Game as fast as possible, **only the other hand** | 5 min | `3` game |
| 6 | Recovery: sit quietly | 3 min | `4` recovery |
| — | Finished | — | `end` |

Order `game_first` (even participant numbers, e.g. p02, p04): the same, but steps 3 and 5 swap
(game first, then math).

Other markers: `note:<text>` for anything unusual (e.g. `note:coughed`, `note:2 was 20 s late`).

## Before the session

1. Open a terminal in `C:\therapy-haptic-wearable` and activate the environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
2. BITalino on, EDA electrodes attached, ACC plugged in. Check the port if unsure:
   ```powershell
   python -m serial.tools.list_ports -v
   ```
3. RT7015 on and worn (fingertip, light pressure, covered from light). Close the vendor app.
   Optional check (should show ~500 samples/s and `valid` beats with an HR):
   ```powershell
   python tools\ppg_check.py --address 70:15:11:69:00:1D
   ```
4. Participant sits comfortably; the sensor arm rests on the table and stays still. The game is
   played with the other hand only.

## Recording

Replace `p01` with the participant id (the ACC is read on A5 by default, `--acc-channel` to change):

```powershell
python tools\run_session.py --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot --sinks none
```

- `--sinks none`: no live output during data collection (no airbag feedback, which would itself
  change the signals). The arousal shown in the window is only a sanity check; baseline and arousal
  are computed offline with `replay_session.py`.
- The order is chosen from the participant number (odd → `math_first`, even → `game_first`);
  `--order game_first` overrides it. The terminal prints the step list.
- Wait for `[EDA] recording ...` and `[PPG] connected to RT7015 ...`.

### Live window (`--plot`)

- **Left:** EDA, ACC, PPG waveform (dots = detected beats), heart rate, arousal and confidence.
  The background colour shows the current condition; dashed lines are the markers.
- **Right, top:** clock, session time, and the current step with a **countdown** and its
  instructions. When the time is up, the box flashes.
- **Start next** button: enters the code of the next step in the protocol order. The five
  condition buttons enter any code (for corrections).
- **Self-report** buttons 1–5 (`sr:N`), **Note** box (Enter saves `note:<text>`).
- **Sensors:** a light per sensor (green = OK, yellow = less than half the expected rate,
  red = no data for 2 s), confidence `c`, baseline progress, state and arousal.
- **End session:** asks for confirmation, enters `end` and stops the recording. Closing the
  window also asks first (it stops without `end`). Ctrl+C in the terminal also stops.

Everything can still be typed in the terminal instead; both go to the same `markers.csv`.

### Flow

1. Settling (3 min): the box counts down. When it flashes, press **Start next: 1 chat**.
2. At the end of each step (box flashes): ask the self-report, press its number, then press
   **Start next**.
3. After step 6 and its self-report: press **End session**. Data is saved to
   `sessions\p01_<date_time>\`.

## After the session

```powershell
python tools\replay_session.py sessions\p01_<date_time>
python tools\make_labels.py sessions\p01_<date_time> --features replay_soft_smooth_gated_chat.csv
```

- `replay_*.png`: overview plot (EDA, heart rate, score, state, confidence) with the markers.
- `features_labeled.csv`: one row per second with `condition` (0 rest, 1 chat, 2 math, 3 game,
  4 recovery), `condition_name`, `phase_idx` (order of the phase), `after` (the phase before, e.g.
  recovery after math), `sr` (that phase's self-report) and `is_baseline` (seconds that set mu).
- A phase ends at its self-report: the seconds from `sr:N` to the next code are left unlabelled,
  so always enter the self-report **before** starting the next step.

## Notes

- Baseline: offline, `replay_session.py --baseline-source chat` (default) or `rest` uses that phase
  (first 60 s dropped, seconds with c >= 0.8). Chat comes first, so there is no live output
  until the chat step is over (about 7 min in).
- Training samples (offline): the first 60 s after each phase change are dropped (`TRAIN_DROP_S`
  in `settings.py`), so the 60 s feature windows lie fully inside the phase.
- Talking and moving affect EDA and heart rate; the confidence `c` drops when the sensor arm
  moves.
