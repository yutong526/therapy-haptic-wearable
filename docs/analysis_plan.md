# Analysis plan (decisions so far)

Offline analysis of the data collected with `docs/testing_process.md`. Numbers are in
`biofeedback/settings.py`; the protocol is in `config/protocol.yaml`. The scripts are not written yet.

## Use in practice (what the learning is for)

- Every second: features x (EDA level, EDA slope, SCR rate, HR) → z = (x − mu) / sigma →
  p = sigmoid(w · z + b) → smoothed arousal → state machine → airbag.
- Learned offline, used live: weights w, bias b, sigma (profile, `profiles/<id>.json`).
  Population model first; per-person calibration from one data-collection session.
- Not learned: mu (measured at the start of every therapy session), smoothing, state thresholds.
- Therapy session (`config/therapy.yaml`): 3 min settling, 3 min opening small talk = baseline
  (therapist presses `1 chat`, then `5 therapy`), then therapy. The live baseline is computed
  exactly as in data collection.

## Baseline

- Source: `chat` (default, `BASELINE_SOURCE`), because therapy involves talking; `rest` is kept to
  compare false alarms during talking (`replay_session.py --baseline-source chat|rest`).
- From the phase: first `BASELINE_SKIP_S` = 60 s dropped, then seconds with c ≥ 0.8, at least 60.
  Not enough → the phase is not used (a lower source or the time-based baseline stays).
- The phase ends at its self-report `sr:N` (answering time is not used).
- During data collection there is no live output (`--sinks none`); baseline and arousal are
  computed offline by replay.

## Labels (`tools/make_labels.py` → `features_labeled.csv`)

- `condition` / `condition_name`: what the participant is doing (0 rest, 1 chat, 2 math, 3 game,
  4 recovery), not the training label.
- `phase_idx`, `after` (phase before, e.g. recovery after math), `sr` (that phase's self-report),
  `is_baseline` (seconds that set mu; excluded from evaluation).
- Seconds from `sr:N` to the next code are unlabelled.

## Training samples

- `TRAIN_DROP_S` = 60 s dropped after each phase change, so the 60 s feature windows lie fully
  inside the phase. Left per phase: chat 3 min, rest 2 min, math / game 4 min, recovery 2 min.
- Label maps (to do, item 4 of the original plan): A math, B game, C math + game vs rest + chat;
  recovery never a training sample.

## Per-person calibration: train / test split

- Each phase (after the 60 s drop) is split in time: first half train, second half test.
- Gap between the halves: **30 s** (`CALIB_GAP_S`) for the main result; the analysis is run again
  with **60 s** (`CALIB_GAP_CHECK_S`, no window overlap) for comparison. Both are reported; the
  30 s result may be slightly optimistic (test windows can reach 30 s into the train half).
- With the 60 s gap a phase may have no samples left: allowed (empty). For testing, rest and chat
  are pooled into one calm class.

## E1: calibration amount

- Amounts per class: **0** (population model only, the starting point), **30 s**, **60 s**, and
  the **whole train half** (`E1_AMOUNTS_S`), all taken from the train halves.
- At each amount the calm class (chat + rest train halves) and the task class get the same
  duration. If there is not enough calm data, all of it is used and this is noted in the results.

## Evaluation details

- Calibration fallback (to the population model): **balanced accuracy** < 70 % (mean of the
  per-class accuracies; the classes are not the same size), or any EDA weight < 0.05.
- AUC is reported twice: samples every 10 s (overlapping 60 s windows, optimistic) and every
  60 s (no overlap).
- The state machine's state 3 is `state_recovery` (arousal falling); the protocol condition
  `recovery` (code 4) is the quiet step after a task. Recovery analysis checks how often the
  state is `state_recovery` during the `recovery` condition.
- Experiment logistics (timer, scoring, buzzer for the arithmetic) are outside this code.
