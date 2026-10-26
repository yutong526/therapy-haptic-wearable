// MPU6050 (GY-521) + RT7015 PPG bring-up on ESP32-S3-DevKitC-1.
// IMU: raw register access with Wire. PPG: AOUT and SOUT read as analog on ADC1.
// Output mode is switched by sending one character: 't' = Teleplot (50 Hz), 'r' = raw CSV (100 Hz).
// Lines in both modes: B (heart beat from the HR algorithm), E (SOUT rising edge),
// P (PPG 1 s summary), S (IMU 1 s summary), WARN/ERROR.

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

// ---------------- Pins and settings ----------------
const int      PIN_SDA          = 8;
const int      PIN_SCL          = 9;
const uint32_t I2C_FREQ_HZ      = 400000;
const uint16_t I2C_TIMEOUT_MS   = 10;      // a stuck bus must not block the loop
const uint32_t SERIAL_BAUD      = 115200;

const uint8_t  MPU_ADDR         = 0x68;    // AD0 unconnected -> 0x68
const uint8_t  WHO_AM_I_GENUINE = 0x68;

const uint32_t SAMPLE_PERIOD_US = 10000;   // 100 Hz
const uint32_t PLOT_EVERY_N     = 2;       // 100 Hz / 2 = 50 Hz Teleplot
const uint32_t SUMMARY_EVERY_N  = 100;     // 100 samples = 1 s
const uint32_t CALIB_MS         = 2000;    // gyro bias averaging time
const uint32_t RETRY_MS         = 1000;    // re-init / error print interval
const int      IMU_INIT_TRIES   = 5;       // then continue without IMU (loop keeps retrying)
const bool     ENABLE_IMU_PLOT  = true;   // IMU Teleplot lines in 't' mode

// PPG (RT7015): both pins on ADC1, full-range attenuation (core 3.x name: ADC_11db = 12 dB)
const int      PIN_PPG_AOUT     = 1;       // GPIO1 = ADC1_CH0, analog pulse wave
const int      PIN_PPG_SOUT     = 2;       // GPIO2 = ADC1_CH1, pulse square wave
const int      PPG_AVG_N        = 8;       // AOUT readings averaged per tick
const int      SOUT_AVG_N       = 4;       // SOUT readings averaged per tick
const int      SOUT_HIGH_MV     = 2000;    // hysteresis: goes high above this
const int      SOUT_LOW_MV      = 1000;    // hysteresis: goes low below this
const int      PPG_BASELINE_N   = 100;     // moving-average length (100 ticks = 1 s)
const float    PPG_SMOOTH_ALPHA = 0.3f;    // EMA smoothing after baseline removal
const int      PPG_CLIP_LOW_MV  = 50;      // min below this -> clipped at 0 V
const int      PPG_CLIP_HIGH_MV = 3200;    // max above this -> clipped at 3.3 V
const int      PPG_FLAT_MV      = 20;      // peak-to-peak below this -> flat (no contact?)

// Heart rate: adaptive double threshold, ported from rt7015_uno.ino (there 500 Hz, here 100 Hz)
const int      HR_SOURCE        = 0;       // 0 = AOUT, 1 = SOUT
const int      HR_FS            = 1000000 / SAMPLE_PERIOD_US;  // 100 Hz
const int      HR_BOX_N         = HR_FS / 50;  // 20 ms moving average nulls 50 Hz mains (2 points)
const float    HR_MIN_AMP_MV    = 50;      // envelope range below this -> no valid signal
const int      HR_MIN_BPM       = 35;      // longer intervals are invalid; no beat this long -> bpm 0
const int      HR_MAX_BPM       = 180;     // sets the minimum beat interval
const int      HR_MIN_WIDTH_MS  = 40;      // signal must stay below low / above high threshold this long
const float    HR_DECAY_S       = 2.0f;    // peak/valley envelope shrinks fully in about this time
const float    HR_HI_FRAC       = 0.6f;    // high threshold = valley + 60 % of range
const float    HR_LO_FRAC       = 0.4f;    // low threshold  = valley + 40 % of range
const int      HR_IV_N          = 8;       // beat intervals kept
const int      HR_MEDIAN_N      = 5;       // median over the most recent N intervals
const int      HR_MIN_IV        = 3;       // intervals needed before reporting a bpm
const float    HR_MAX_SPREAD    = 1.5f;    // longest > 1.5 x shortest -> motion/noise, bpm 0

// MPU6050 registers
const uint8_t REG_SMPLRT_DIV   = 0x19;
const uint8_t REG_CONFIG       = 0x1A;
const uint8_t REG_GYRO_CONFIG  = 0x1B;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;
const uint8_t REG_PWR_MGMT_1   = 0x6B;
const uint8_t REG_WHO_AM_I     = 0x75;

// Register values and scale factors
const uint8_t ACCEL_FS_4G      = 0x08;     // AFS_SEL = 1 -> +/-4 g
const uint8_t GYRO_FS_500DPS   = 0x08;     // FS_SEL = 1 -> +/-500 dps
const uint8_t DLPF_44HZ        = 0x03;     // DLPF_CFG = 3 -> accel 44 Hz, gyro 42 Hz
const uint8_t SMPLRT_100HZ     = 9;        // 1 kHz / (1 + 9) = 100 Hz
const float   ACCEL_LSB_PER_G  = 8192.0f;
const float   GYRO_LSB_PER_DPS = 65.5f;

// ---------------- State ----------------
float gyroBias[3] = {0, 0, 0};
uint32_t nextSampleUs = 0;
uint32_t tick = 0;
uint32_t lastErrorMs = 0;
uint32_t failCount = 0;

// Accumulators for the 1 s summary
uint32_t sumN = 0;
float sumAccMag = 0, sumMotionSq = 0, sumGyroSq = 0;

char outputMode = 't';  // 't' Teleplot, 'r' raw CSV

// PPG filter state
float ppgBuf[PPG_BASELINE_N];
float ppgBufSum = 0;
int ppgBufIdx = 0, ppgBufCount = 0;
float ppgSmooth = 0;

// SOUT edge detector state
bool soutHigh = false;
uint32_t lastRiseMs = 0, riseMs = 0, lastHighMs = 0;

// Heart-rate detector state (times in samples)
float hrBox[HR_BOX_N];
float hrBoxSum = 0, hrMax = 0, hrMin = 0;
int hrBoxPos = 0;
bool hrInit = false, hrArmed = false;
uint32_t hrN = 0, hrLastBeat = 0, hrCrossN = 0, hrLastIv = 0;
uint32_t hrIv[HR_IV_N];
int hrIvCnt = 0, hrIvPos = 0, hrLoCnt = 0, hrHiCnt = 0;
int hrBpm = 0;

// PPG 1 s summary
float ppgMin = 1e9f, ppgMax = -1e9f, ppgSum = 0;
uint32_t ppgN = 0, soutEdges = 0;

// ---------------- I2C helpers ----------------
bool writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t reg, uint8_t *buf, size_t len) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)len) != len) return false;
  for (size_t i = 0; i < len; i++) buf[i] = Wire.read();
  return true;
}

void i2cScan() {
  Serial.println("I2C scan:");
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  found device at 0x%02X\n", addr);
      found++;
    }
  }
  if (found == 0) Serial.println("  no devices found - check wiring and power");
}

// ---------------- MPU6050 ----------------
// Reads WHO_AM_I and writes the configuration. Returns false on any I2C error.
bool mpuInit() {
  uint8_t who = 0;
  if (!readRegs(REG_WHO_AM_I, &who, 1)) {
    Serial.println("ERROR: cannot read WHO_AM_I");
    return false;
  }
  Serial.printf("WHO_AM_I = 0x%02X\n", who);
  if (who != WHO_AM_I_GENUINE) {
    Serial.printf("WARNING: expected 0x%02X, probably a clone chip - continuing anyway\n", WHO_AM_I_GENUINE);
  }

  bool ok = writeReg(REG_PWR_MGMT_1, 0x00);   // wake up
  delay(100);                                 // let the chip settle after wake-up
  ok &= writeReg(REG_SMPLRT_DIV, SMPLRT_100HZ);
  ok &= writeReg(REG_CONFIG, DLPF_44HZ);
  ok &= writeReg(REG_GYRO_CONFIG, GYRO_FS_500DPS);
  ok &= writeReg(REG_ACCEL_CONFIG, ACCEL_FS_4G);
  if (!ok) Serial.println("ERROR: configuration write failed");
  return ok;
}

// Burst-reads 14 bytes from 0x3B and converts to g and deg/s (bias not removed).
bool mpuRead(float acc[3], float gyro[3]) {
  uint8_t b[14];
  if (!readRegs(REG_ACCEL_XOUT_H, b, sizeof(b))) return false;
  for (int i = 0; i < 3; i++) {
    acc[i]  = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]) / ACCEL_LSB_PER_G;
    gyro[i] = (int16_t)((b[8 + 2 * i] << 8) | b[9 + 2 * i]) / GYRO_LSB_PER_DPS;  // skip temp at 6..7
  }
  return true;
}

void calibrateGyro() {
  Serial.println("Keep the board STILL - measuring gyro bias for 2 s...");
  delay(500);  // give the user a moment
  double sum[3] = {0, 0, 0};
  uint32_t n = 0;
  uint32_t start = millis();
  while (millis() - start < CALIB_MS) {
    float a[3], g[3];
    if (mpuRead(a, g)) {
      for (int i = 0; i < 3; i++) sum[i] += g[i];
      n++;
    }
    delay(5);  // calibration only, not part of the sampling loop
  }
  if (n == 0) {
    Serial.println("ERROR: no gyro samples during calibration, bias set to 0");
    return;
  }
  for (int i = 0; i < 3; i++) gyroBias[i] = sum[i] / n;
  Serial.printf("Gyro bias (dps, %lu samples): x=%.3f y=%.3f z=%.3f\n",
                (unsigned long)n, gyroBias[0], gyroBias[1], gyroBias[2]);
}

// ---------------- PPG ----------------
float readMilliVoltsAvg(int pin, int n) {
  uint32_t sum = 0;
  for (int i = 0; i < n; i++) sum += analogReadMilliVolts(pin);
  return (float)sum / n;
}

// Removes the ~1 s moving average (baseline), then applies light EMA smoothing.
float ppgFilter(float mv) {
  if (ppgBufCount == PPG_BASELINE_N) ppgBufSum -= ppgBuf[ppgBufIdx];
  else ppgBufCount++;
  ppgBuf[ppgBufIdx] = mv;
  ppgBufSum += mv;
  ppgBufIdx = (ppgBufIdx + 1) % PPG_BASELINE_N;
  float ac = mv - ppgBufSum / ppgBufCount;
  ppgSmooth += PPG_SMOOTH_ALPHA * (ac - ppgSmooth);
  return ppgSmooth;
}

// Software Schmitt trigger on SOUT; prints E,t_ms,ibi_ms,bpm,high_ms on each rising edge.
// Time resolution is one tick (10 ms). First edge prints ibi_ms = 0, bpm = 0.
void soutUpdate(float mv, uint32_t nowMs) {
  if (!soutHigh && mv > SOUT_HIGH_MV) {
    soutHigh = true;
    uint32_t ibi = lastRiseMs ? nowMs - lastRiseMs : 0;
    Serial.printf("E,%lu,%lu,%.1f,%lu\n", (unsigned long)nowMs, (unsigned long)ibi,
                  ibi ? 60000.0f / ibi : 0.0f, (unsigned long)lastHighMs);
    lastRiseMs = riseMs = nowMs;
    soutEdges++;
  } else if (soutHigh && mv < SOUT_LOW_MV) {
    soutHigh = false;
    lastHighMs = nowMs - riseMs;
  }
}

// ---------------- Heart rate ----------------
// Feeds one sample (mV). Returns true on a detected beat; hrBpm is updated on each beat
// and is 0 while there is no stable heart rate.
bool hrProcess(float x) {
  const uint32_t minWidth = HR_MIN_WIDTH_MS * HR_FS / 1000;
  const uint32_t minIv = HR_FS * 60 / HR_MAX_BPM;
  const uint32_t maxIv = HR_FS * 60 / HR_MIN_BPM;

  hrN++;
  if (!hrInit) {
    for (int i = 0; i < HR_BOX_N; i++) hrBox[i] = x;
    hrBoxSum = HR_BOX_N * x;
    hrMax = hrMin = x;
    hrInit = true;
    return false;
  }

  // Moving average against mains interference
  hrBoxSum += x - hrBox[hrBoxPos];
  hrBox[hrBoxPos] = x;
  hrBoxPos = (hrBoxPos + 1) % HR_BOX_N;
  float y = hrBoxSum / HR_BOX_N;

  // Envelope: peak and valley creep toward each other to follow baseline drift
  float decay = (hrMax - hrMin) / (HR_DECAY_S * HR_FS);
  if (y > hrMax) hrMax = y; else hrMax -= decay;
  if (y < hrMin) hrMin = y; else hrMin += decay;
  float range = hrMax - hrMin;

  bool beat = false;
  if (range >= HR_MIN_AMP_MV) {
    // Double threshold with hysteresis: arm below low, beat after staying above high
    float hi = hrMin + HR_HI_FRAC * range;
    float lo = hrMin + HR_LO_FRAC * range;
    if (y < lo) { if (hrLoCnt < 60000) hrLoCnt++; } else hrLoCnt = 0;
    if (hrLoCnt >= (int)minWidth) hrArmed = true;
    if (y > hi) { if (hrHiCnt == 0) hrCrossN = hrN; if (hrHiCnt < 60000) hrHiCnt++; } else hrHiCnt = 0;
    uint32_t since = hrCrossN - hrLastBeat;
    if (hrArmed && hrHiCnt >= (int)minWidth && since > minIv) {
      hrArmed = false;
      beat = true;
      if (hrLastBeat != 0 && since <= maxIv) {
        hrIv[hrIvPos] = hrLastIv = since;
        hrIvPos = (hrIvPos + 1) % HR_IV_N;
        if (hrIvCnt < HR_IV_N) hrIvCnt++;
      } else {
        hrIvCnt = 0;  // implausible interval: start counting again
        hrLastIv = 0;
      }
      hrLastBeat = hrCrossN;  // beat time = moment the high threshold was crossed
    }
  }

  // No beat for longer than the longest allowed interval -> reset
  if (hrN - hrLastBeat > maxIv) { hrIvCnt = 0; hrBpm = 0; }

  if (beat) {
    if (hrIvCnt >= HR_MIN_IV) {
      // Median of the most recent intervals (insertion sort)
      int k = min(hrIvCnt, HR_MEDIAN_N);
      uint32_t v[HR_MEDIAN_N];
      for (int i = 0; i < k; i++) v[i] = hrIv[(hrIvPos + HR_IV_N - 1 - i) % HR_IV_N];
      for (int i = 1; i < k; i++) {
        uint32_t t = v[i];
        int j = i - 1;
        while (j >= 0 && v[j] > t) { v[j + 1] = v[j]; j--; }
        v[j + 1] = t;
      }
      // Intervals jumping around -> interference or motion, no result
      hrBpm = (v[k - 1] > HR_MAX_SPREAD * v[0]) ? 0 : (int)(60.0f * HR_FS / v[k / 2] + 0.5f);
    } else {
      hrBpm = 0;
    }
  }
  return beat;
}

void ppgSummary(uint32_t nowMs) {
  if (ppgN == 0) return;
  Serial.printf("P,%lu,%.0f,%.0f,%.0f,%lu\n", (unsigned long)nowMs, ppgMin, ppgMax,
                ppgSum / ppgN, (unsigned long)soutEdges);
  if (ppgMin < PPG_CLIP_LOW_MV || ppgMax > PPG_CLIP_HIGH_MV)
    Serial.println("WARN: PPG looks clipped (near 0 or 3300 mV)");
  else if (ppgMax - ppgMin < PPG_FLAT_MV)
    Serial.println("WARN: PPG almost flat (no contact? module off?)");
  ppgMin = 1e9f; ppgMax = -1e9f; ppgSum = 0;
  ppgN = soutEdges = 0;
}

void checkSerialCommand() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 't' || c == 'r') {
      outputMode = c;
      Serial.printf("# mode=%c\n", c);
    }
  }
}

// ---------------- Main ----------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);
  Serial.println("\n=== MPU6050 bring-up ===");

  // Idle I2C lines must be HIGH via the GY-521 pull-ups (internal pull-ups off here).
  // LOW means the module is not powered, a wire is on the wrong pin, or a line is shorted.
  pinMode(PIN_SDA, INPUT);
  pinMode(PIN_SCL, INPUT);
  delay(10);
  Serial.printf("Line check (expect 1/1): SDA(GPIO%d)=%d SCL(GPIO%d)=%d\n",
                PIN_SDA, digitalRead(PIN_SDA), PIN_SCL, digitalRead(PIN_SCL));

  Wire.begin(PIN_SDA, PIN_SCL, I2C_FREQ_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);

  // Retry a few times; print the scan each time to help debugging.
  for (int i = 0; i < IMU_INIT_TRIES; i++) {
    i2cScan();
    if (mpuInit()) break;
    Serial.println("Retrying in 1 s...");
    delay(RETRY_MS);
  }

  calibrateGyro();

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_PPG_AOUT, ADC_11db);
  analogSetPinAttenuation(PIN_PPG_SOUT, ADC_11db);
  Serial.println("PPG ready. Send 't' (Teleplot) or 'r' (raw CSV).");

  nextSampleUs = micros();
}

void loop() {
  // Wait for the next 10 ms slot (signed compare handles micros() wrap-around)
  if ((int32_t)(micros() - nextSampleUs) < 0) return;
  nextSampleUs += SAMPLE_PERIOD_US;
  // If we fell far behind (e.g. long I2C timeout), resync instead of bursting
  if ((int32_t)(micros() - nextSampleUs) > (int32_t)SAMPLE_PERIOD_US) nextSampleUs = micros();
  tick++;
  uint32_t nowMs = millis();
  checkSerialCommand();

  // PPG
  float ppgMv  = readMilliVoltsAvg(PIN_PPG_AOUT, PPG_AVG_N);
  float soutMv = readMilliVoltsAvg(PIN_PPG_SOUT, SOUT_AVG_N);
  float ppgFilt = ppgFilter(ppgMv);
  soutUpdate(soutMv, nowMs);
  if (hrProcess(HR_SOURCE == 0 ? ppgMv : soutMv)) {
    // B,t_ms,ibi_ms,bpm  (t_ms = threshold crossing; ibi_ms 0 = first/invalid interval)
    uint32_t crossMs = nowMs - (hrN - hrCrossN) * (SAMPLE_PERIOD_US / 1000);
    Serial.printf("B,%lu,%lu,%d\n", (unsigned long)crossMs,
                  (unsigned long)(hrLastIv * (SAMPLE_PERIOD_US / 1000)), hrBpm);
  }
  ppgMin = min(ppgMin, ppgMv);
  ppgMax = max(ppgMax, ppgMv);
  ppgSum += ppgMv;
  ppgN++;

  if (outputMode == 't' && tick % PLOT_EVERY_N == 0) {
    Serial.printf(">ppg_mv:%.0f\n>ppg_filt:%.1f\n>sout_mv:%.0f\n", ppgMv, ppgFilt, soutMv);
  }

  // IMU (values stay NAN if the read fails)
  float a[3] = {NAN, NAN, NAN}, g[3] = {NAN, NAN, NAN};
  if (mpuRead(a, g)) {
    failCount = 0;
    for (int i = 0; i < 3; i++) g[i] -= gyroBias[i];
    float accMag  = sqrtf(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
    float gyroMag = sqrtf(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);

    sumN++;
    sumAccMag   += accMag;
    sumMotionSq += (accMag - 1.0f) * (accMag - 1.0f);
    sumGyroSq   += gyroMag * gyroMag;

    if (ENABLE_IMU_PLOT && outputMode == 't' && tick % PLOT_EVERY_N == 0) {
      Serial.printf(">ax:%.4f\n>ay:%.4f\n>az:%.4f\n>acc_mag:%.4f\n>gx:%.3f\n>gy:%.3f\n>gz:%.3f\n",
                    a[0], a[1], a[2], accMag, g[0], g[1], g[2]);
    }
  } else {
    failCount++;
    // Rate-limit the error message and try to re-init (chip may have reset/slept)
    if (millis() - lastErrorMs >= RETRY_MS) {
      lastErrorMs = millis();
      Serial.printf("ERROR: I2C read failed (%lu in a row), re-initialising\n", (unsigned long)failCount);
      mpuInit();
    }
  }

  if (outputMode == 'r') {
    Serial.printf("R,%lu,%.0f,%.0f,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f\n", (unsigned long)nowMs,
                  ppgMv, soutMv, a[0], a[1], a[2], g[0], g[1], g[2]);
  }

  if (tick % SUMMARY_EVERY_N == 0) {
    ppgSummary(nowMs);
    if (sumN > 0) {
      Serial.printf("S,%lu,%.4f,%.4f,%.3f\n", (unsigned long)millis(),
                    sumAccMag / sumN, sqrtf(sumMotionSq / sumN), sqrtf(sumGyroSq / sumN));
    } else {
      Serial.printf("S,%lu,nan,nan,nan\n", (unsigned long)millis());
    }
    sumN = 0;
    sumAccMag = sumMotionSq = sumGyroSq = 0;
  }
}
