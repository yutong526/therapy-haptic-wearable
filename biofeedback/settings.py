"""All tunable numbers and switches in one place. (placeholder) = not tuned on real data yet."""

FEATURES = ("eda_level", "eda_slope", "scr_rate", "hr")  # x1..x4, also the profile order

# Switches (also settable from the command line of run_session.py / replay_session.py)
CONFIDENCE_MODE = "soft"     # "soft": c in [0, 1]; "hard": c is 0 or 1 (valid flag only)
SMOOTHING = True             # confidence-weighted smoothing of the score
BASELINE_MODE = "gated"      # "fixed", "always" or "gated"

# Session timing (recording keeps everything; these only affect processing)
WARMUP_S = 180               # EDA settling time at the start, not used for anything
BASELINE_S = 120             # rest period after the warm-up that sets the initial mu
BASELINE_MIN_VALID_S = 90    # seconds with c >= C_BASELINE needed; otherwise the period is extended
C_BASELINE = 0.8             # confidence needed to use a second for the baseline (initial and updates)

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

# Heart rate (firmware B lines, one per beat; validity decided here, not on the board)
IBI_MIN_MS, IBI_MAX_MS = 300, 2000
IBI_MAX_DEV = 0.30           # valid beat: within 30 % of the median of the last IBIs
IBI_HISTORY = 5
IBI_MIN_HISTORY = 3          # fewer previous IBIs than this -> beat not valid yet
HR_MAX_GAP_S = 3             # no valid beat for this long -> hr missing (z = 0)

# Motion. Source "bitalino" (ACC sensor, run_session.py --acc-channel): 1 s standard deviation of
# the ACC axis. Source "esp32" (MPU6050, firmware S lines): 1 s RMS of |a| - 1 g.
MOTION_LO_G = 0.02           # (placeholder) at or below: motion confidence 1
MOTION_HI_G = 0.10           # (placeholder) at or above: motion confidence 0
MOTION_HARD_G = 0.05         # (placeholder) hard mode: still if below this
LINK_TIMEOUT_S = 3           # no motion data for this long -> link dropped -> c = 0

# BITalino ACC (datasheet: g = (ADC - Cmin) / (Cmax - Cmin) * 2 - 1), in 10-bit counts.
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
STEADY_AROUSAL = 0.4         # (placeholder) recovery ends when a is below this and the slope z <= 0

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
