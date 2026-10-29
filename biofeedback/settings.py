"""All tunable numbers and switches in one place. (placeholder) = not tuned on real data yet."""

FEATURES = ("eda_level", "eda_slope", "scr_rate", "hr")  # x1..x4, also the profile order

# Switches (also settable from the command line of run_session.py / replay_session.py)
CONFIDENCE_MODE = "soft"     # "soft": c in [0, 1]; "hard": c is 0 or 1 (valid flag only)
SMOOTHING = True             # confidence-weighted smoothing of the score
BASELINE_MODE = "gated"      # "fixed", "always" or "gated"

# Baseline (mu). With condition markers (config/protocol.yaml): mu comes from the seconds of the
# BASELINE_SOURCE condition; until that phase is over, BASELINE_PROVISIONAL is used (None = no
# output until then). Each phase: first BASELINE_SKIP_S dropped, then only seconds with
# c >= C_BASELINE, at least BASELINE_PHASE_MIN_VALID_S of them, otherwise the phase is not used.
BASELINE_SOURCE = "chat"     # "chat" or "rest" (replay_session.py --baseline-source to compare)
BASELINE_PROVISIONAL = "rest"
BASELINE_SKIP_S = 60          # same as TRAIN_DROP_S: the 60 s feature windows lie inside the phase
BASELINE_PHASE_MIN_VALID_S = 60
C_BASELINE = 0.8             # confidence needed to use a second for the baseline (initial and updates)
# Without any condition marker: mu from BASELINE_S after WARMUP_S (extended until enough valid seconds)
WARMUP_S = 180               # EDA settling time at the start, not used for anything
BASELINE_S = 120
BASELINE_MIN_VALID_S = 90

# EDA (BITalino, datasheet Rev B: uS = ADC / 2^10 * 3.3 / 0.132, range 0-25 uS; 1 count = 0.024 uS)
EDA_RATE_HZ = 100
EDA_BIN_MS = 100             # 100 Hz samples are averaged into 10 Hz bins
EDA_SMOOTH_S = 0.5           # moving average over the bins before the features (0.1 = off)
EDA_MIN_US = 0.05            # below: electrode off -> c = 0
EDA_MAX_US = 24.5            # above: saturated -> c = 0
EDA_MAX_RATE_US_PER_S = 10   # faster change between 10 Hz bins: artefact -> c = 0
EDA_MIN_FILL = 0.5           # fraction of the expected samples in the last second, else link dropped

# Feature windows (seconds). A feature needs data over at least WINDOW_MIN_FRACTION of its window.
EDA_LEVEL_WINDOW_S = 30      # x1 mean uS
EDA_SLOPE_WINDOW_S = 60      # x2 linear slope, uS/min
SCR_WINDOW_S = 60            # x3 SCRs per minute
HR_WINDOW_S = 30             # x4 median of the valid beats
SHORT_WINDOW_S = 10          # eda_rise, used only for the spike state
WINDOW_MIN_FRACTION = 0.5

# SCR (online rule, also used offline through features.detect_scrs)
SCR_MIN_AMP_US = 0.1         # rise of at least this ...
SCR_MAX_RISE_S = 5           # ... within this time; counted once it has fallen back by half

# PPG over the RT7015's own BLE link (biofeedback/ppg_ble.py, run_session.py --ppg ble)
PPG_BLE_NAME = "RT7015"      # scan for a device whose name contains this ...
PPG_BLE_ADDRESS = None       # ... unless fixed here, e.g. "70:15:88:88:88:88" (or --ppg-address)
PPG_NOTIFY_UUID = "0000FFF4-0000-1000-8000-00805F9B34FB"
PPG_WRITE_UUID = "0000FFF3-0000-1000-8000-00805F9B34FB"
PPG_START_CMD = bytes.fromhex("AA 04 80 11 01 2F BB")
PPG_STOP_CMD = bytes.fromhex("AA 04 80 11 00 2E BB")
PPG_SCAN_S = 10              # scan timeout per attempt
PPG_RETRY_S = 3              # wait before reconnecting
PPG_RATES = (125, 250, 500, 1000, 2000)  # sample-rate index in the 0x10 frame
PPG_DEFAULT_RATE = 500
PPG_CLOCK_GAIN = 0.01        # sample clock: slow correction towards later packet arrivals (drift)
PPG_RESYNC_MS = 500          # packet this much later than its samples' predicted time -> re-anchor (gap)
PPG_GAP_MS = 100             # larger gap between sample times -> beat detection restarts (no IBI across)
# Beat detection (vendor logic): SOUT above this is "high"; a rising edge is a candidate beat,
# accepted if AOUT at the falling edge minus AOUT at the rising edge exceeds PPG_MIN_PULSE_MV.
PPG_SOUT_HIGH_MV = 1500
PPG_MIN_PULSE_MV = 100       # vendor uses 500; on p01 (2026-10-29, median change 257 mV) 500 left HR
                             # available 8 % of the time, 100 -> 91 % with the same median HR (82)

# Heart rate (one IBI per detected beat; validity decided here, not by the detector)
IBI_MIN_MS, IBI_MAX_MS = 300, 2000
IBI_MAX_DEV = 0.30           # valid beat: within 30 % of the median of the last IBIs
IBI_HISTORY = 5
IBI_MIN_HISTORY = 3          # fewer previous IBIs than this -> beat not valid yet
HR_MAX_GAP_S = 3             # no valid beat for this long -> hr missing (z = 0)

# Motion confidence: 1 s standard deviation of the BITalino ACC axis (g)
MOTION_LO_G = 0.02           # (placeholder) at or below: motion confidence 1
MOTION_HI_G = 0.10           # (placeholder) at or above: motion confidence 0
MOTION_HARD_G = 0.05         # (placeholder) hard mode: still if below this
LINK_TIMEOUT_S = 3           # no ACC data (or no PPG notification) for this long -> link dropped

# BITalino ACC (datasheet: g = (ADC - Cmin) / (Cmax - Cmin) * 2 - 1), in 10-bit counts.
ACC_CHANNEL = 5              # BITalino input of the ACC (run_session.py --acc-channel)
# Measured with tools/acc_check.py (2026-10-28, slow 360 deg rotation); redo if the sensor changes.
ACC_CMIN = 415             # ADC with the axis pointing down (-1 g)
ACC_CMAX = 668             # ADC with the axis pointing up (+1 g)

# Live loop
ARRIVAL_MARGIN_MS = 200      # each step uses data that arrived at least this long before it (live = replay)
VALID_C = 0.2               # valid = c >= VALID_C
ALPHA = 0.3                  # a <- a + ALPHA * c * (p - a)
Z_CLIP = 5.0
MIN_DWELL_S = 5              # every state lasts at least this long
SPIKE_RISE_US = 0.2          # (placeholder) EDA rise over SHORT_WINDOW_S -> spike
RISE_AROUSAL = 0.6           # (placeholder) a at least this -> gradual rise
RISE_SLOPE_Z = 1.0           # (placeholder) or z of eda_slope at least this -> gradual rise
STEADY_AROUSAL = 0.4         # (placeholder) state_recovery ends when a is below this and the slope z <= 0

# Baseline update (mu only)
BASELINE_BETA = 1 / 600      # per second
BASELINE_FROZEN_S = 600      # gated: frozen longer than this -> update anyway at BETA * SLOW_FACTOR
BASELINE_SLOW_FACTOR = 0.1

# Defaults when there is no profile (placeholders until calibration)
DEFAULT_WEIGHTS = {"eda_level": 0.5, "eda_slope": 1.0, "scr_rate": 1.0, "hr": 0.5}
DEFAULT_BIAS = -1.0
DEFAULT_SIGMA = {"eda_level": 0.3, "eda_slope": 0.5, "scr_rate": 2.0, "hr": 5.0}  # also the sigma floor
DEFAULT_LAG_S = 0            # used only to align labels in training, not live

# Live output
UDP_TARGET = "127.0.0.1:5005"

# Test protocol (steps, durations, order, condition codes): config/protocol.yaml

# Offline training samples (label maps, not live): after each phase change this many seconds are
# dropped, so the 60 s feature windows (eda_slope, scr_rate) lie fully inside the new phase
TRAIN_DROP_S = 60
# Per-person calibration: each phase (after TRAIN_DROP_S) is split in time, first half train, second
# half test, with a gap between so test windows overlap the train half less (docs/analysis_plan.md)
CALIB_GAP_S = 30             # main result (test windows may still reach 30 s into the train half)
CALIB_GAP_CHECK_S = 60       # second run for comparison (no overlap; some phases may be empty)
# E1: calibration amount per class (calm = chat + rest train halves, task = math / game train half),
# equal for both classes; 0 = population model only, None = the whole train half
E1_AMOUNTS_S = (0, 30, 60, None)

# Live window (run_session.py --plot); display only, nothing here affects the processing
PLOT_WINDOW_S = 120          # EDA, motion, heart rate and arousal panels show this many seconds
PLOT_PPG_WINDOW_S = 10       # PPG waveform panel (500 Hz) shows this many seconds
PLOT_REFRESH_MS = 250
PLOT_STALE_S = 2             # sensor light red when no data for this long; yellow below half the rate
