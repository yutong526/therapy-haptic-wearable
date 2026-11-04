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

Replace `<name>` with the participant's name (the folder becomes `sessions\<date>_<name>`) and `p01` with their anonymous id: odd ids do math first, even ids the game first, and the id is what the analysis uses. The ACC is read on A5 by default (`--acc-channel` to change):

```powershell
python tools\run_session.py --name <name> --subject p01 --eda COM15 --ppg-address 70:15:11:69:00:1D --plot --sinks none
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
- **Pause / Resume** button (or type `pause` / `resume`): for an interruption (e.g. fixing a
  sensor). Recording continues, the countdown stops, the box turns yellow and the plots are
  hatched; these seconds are left out of the labels and the baseline. The same step continues
  after Resume. A new code, a self-report or `end` also ends a pause.
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
   `sessions\<date>_<name>\`.

### Continuing a stopped session

If the program stopped too early (closed, crashed, or End pressed by mistake), continue in the
same folder:

```powershell
python tools\run_session.py --resume sessions\<date>_<name> --plot --sinks none
```

- Subject, order, ports and settings are taken from the session (a port can be given again if
  it changed, e.g. `--eda COM16`).
- The recorded data is replayed first to restore the baseline and the protocol step (this takes
  a few seconds for a long session). The time axis continues; the stopped time is recorded as a
  pause. A step that was only ended by `end` (no self-report yet) continues with its remaining
  time.
- Let the electrodes settle again if they were taken off.

## After the session

```powershell
python tools\replay_session.py sessions\<date>_<name>
python tools\make_labels.py sessions\<date>_<name> --features replay_soft_smooth_gated_chat.csv
```

- `replay_*.png`: overview plot (EDA, heart rate, score, state, confidence) with the markers.
- `features_labeled.csv`: one row per second with `condition` (0 rest, 1 chat, 2 math, 3 game,
  4 recovery), `condition_name`, `phase_idx` (order of the phase), `after` (the phase before, e.g.
  recovery after math), `sr` (that phase's self-report) and `is_baseline` (seconds that set mu).
- A phase ends at its self-report: the seconds from `sr:N` to the next code (answering) are left
  unlabelled. Best: enter the self-report **before** starting the next step. A self-report entered
  within 60 s **after** a step started (`SR_LATE_S`) still counts for the step before and does not
  end the new one; so a self-report pressed by mistake in the first minute of a step is harmless.
  (All steps are at least 3 min, so a step's own self-report never falls in this window.)

## Airbag test (one body part per run)

Effect of the airbag on EDA, heart rate and motion at rest. Steps in `config/airbag_test.yaml`
(settling 3 min, rest 2, airbag 3, rest 3, airbag 3, rest 2 min). The steps run by themselves
(`auto_steps: true`): `0` / `6` are entered when the time is up, `end` after the last step stops the
recording; `Pause` stops the countdown, `--manual-steps` goes back to pressing them by hand. The pump
starts at `6` and stops (deflates) at the next code, `sr:N`, `pause` or `end`.

```powershell
python tools\run_session.py --name <name> --site wrist --protocol config\airbag_test.yaml --eda COM15 --ppg-address 70:15:11:69:00:1D --esp32 COM16 --sinks none --plot
python tools\analyze_airbag.py sessions\<date>_<name>_wrist
python tools\analyze_airbag.py sessions\<date>_<name>_wrist sessions\<date>_<name>_upper_arm   # compare sites
```

- Before the first run with a bag (`tools\pump_console.py COM11 --name <site>`):
  1. With the bag empty, `?` shows the zero offset (about 0 kPa; `z` to zero again).
  2. `c1`, `c2`, `c3`, ... (kPa): constant pressure steps; ask the participant and log
     `note:<site> <kPa> <comment>`. Stay well below `MAX_KPA` (8 kPa) in the firmware.
  3. `b`, with `e` / `i` set to the chosen values: check the breaths follow the target (live
     plot); if they lag or overshoot, change Kp (`p`) / Ki (`n`). `x`: the bag empties.
  4. Put the chosen values in `PUMP_EXHALE_KPA` / `PUMP_INHALE_KPA` in `settings.py`; use the same
     values for all sites, so the sites are compared at the same pressure.
- Put the airbag on and empty before the settling time; note where the EDA electrodes and the
  BITalino ACC are relative to the airbag (`note:...`).
- `analyze_airbag.py` prints per block: airbag pressure (mean / max kPa), EDA mean, EDA change over the block, largest EDA rise
  within 10 s of the start (startle), SCRs per minute, heart rate, motion, share of valid seconds;
  and each airbag block minus the rest before it. A few blocks per site: a description, not a
  tested effect.

## Notes

- Baseline: offline, `replay_session.py --baseline-source chat` (default) or `rest` uses that phase
  (first 60 s dropped, seconds with c >= 0.8). Chat comes first, so there is no live output
  until the chat step is over (about 7 min in).
- Training samples (offline): the first 60 s after each phase change are dropped (`TRAIN_DROP_S`
  in `settings.py`), so the 60 s feature windows lie fully inside the phase.
- Talking and moving affect EDA and heart rate; the confidence `c` drops when the sensor arm
  moves.
