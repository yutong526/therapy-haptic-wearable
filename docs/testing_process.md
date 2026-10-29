# Testing process

One session takes about **18 min**: 3 min settling, then the six steps below (15 min).

## Steps and markers

Type the marker number and press **Enter** at the **start** of each step (in the terminal where the
recording runs). The program confirms with `[marker ...]`.

| Step | Activity | Duration | Marker |
|---|---|---|---|
| — | Settling: sit quietly, sensors on (not used) | 3 min | — |
| 1 | Sitting quietly, no talking | 2 min | `0` |
| 2 | Chat | 1 min | `1` |
| 3 | Math questions | 5 min | `2` |
| 4 | Sitting quietly | 1 min | `0` |
| 5 | Game, as fast as you can | 5 min | `3` |
| 6 | Sitting quietly | 1 min | `0` |
| — | Finished | — | `end`, then Ctrl+C |

Marker meaning: `0` rest, `1` chat, `2` math, `3` game.

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
4. Participant sits comfortably; arm with the sensors rests on the table and stays still except
   during the game.

## Recording

Replace `p01` with the participant id and `5` with the BITalino input of the ACC:

```powershell
python tools\run_session.py --subject p01 --eda COM15 --acc-channel 5 --ppg-address 70:15:11:69:00:1D
```

- Wait for `[EDA] recording ...` and `[PPG] connected to RT7015 ...`.
- The first 3 min are settling (status lines show `warmup`). When the status line changes to
  `baseline`, type `0` and start **step 1**. The program uses these 2 min as the personal baseline.
- Follow the table, typing each marker at the start of its step.
- After step 6 type `end`, then press **Ctrl+C**. Data is saved to `sessions\p01_<date_time>\`.

## After the session

```powershell
python tools\replay_session.py sessions\p01_<date_time>
python tools\make_labels.py sessions\p01_<date_time> --labels config\labels.protocol.yaml
```

- `replay_*.png`: overview plot (EDA, heart rate, score, state, confidence) with the markers.
- `features_labeled.csv`: one row per second with a `condition` column (0 rest, 1 chat, 2 math,
  3 game) taken from the markers.

## Notes

- If a marker is typed late or wrong, type a note (e.g. `0 was 20 s late`) and keep going; the
  markers file can be corrected afterwards.
- Talking and moving (chat, game) affect EDA and heart rate; the confidence `c` drops when the
  arm moves, which is expected during the game.
