// Airbag pump for the haptic armband on a classic ESP32 DevKit (from the team's pump.ino).
// Pump 1 inflates, pump 2 deflates (dual H-bridge: PWMA/AIN1/AIN2, PWMB/BIN1/BIN2, STBY); no valve.
// Pressure: XGZP6847A (0-100 kPa gauge) on GPIO34.
//
// Breathing (default, mode "b"), open loop: after "s" straight to INHALE (pump 1 at inhalePwm for
// INHALE_MS, off early once the pressure reaches inhaleKpa) / EXHALE (pump 2 at exhalePwm for
// EXHALE_MS, off early once below exhaleKpa), repeated. A PI controller was too weak here: the bag
// on the table reached only 0.7 kPa and the PI was far from full PWM within a 4 s inhale (2026-11-04).
// Constant mode ("c"): FILL (must reach holdKpa within FILL_TIMEOUT_MS), then HOLD; a PI controller
// at 100 Hz drives pump 1 (output > 0) or pump 2 (output < 0) towards holdKpa.
// "x" -> DEFLATE (pump 2 full) until below emptyKpa (may be negative: suction), below the sensor
// range, or DEFLATE_MAX_MS -> IDLE.
//
// Link: USB serial (115200) and Bluetooth serial (SPP, device name BT_NAME; pair it in Windows,
// then use its outgoing COM port). Both work at the same time with the same commands and output.
// Single characters, run at once (when no line command is being typed):
//   s  start (only from IDLE; repeats ignored; blocked after a fault until the next x)
//   x  stop with deflation (also clears a fault)
//   h  heartbeat from the PC (run_session.py sends "h" + "s"/"x" every second). After the
//      first h, no h for LINK_TIMEOUT_MS while running -> stop as with x.
// Line commands (end with Enter; serial monitor set to "Newline"):
//   e<kPa>  exhale stop pressure, may be negative (suction)    i<kPa>  inhale stop pressure
//   a<pwm>  inhale PWM (pump 1)      d<pwm>  exhale PWM (pump 2)     (0-255, breathing)
//   c<kPa>  constant mode at this target      b  breathing mode (default)
//   p<val>  Kp (PWM per kPa)        n<val>  Ki (PWM per kPa*s)     (constant mode)
//   v<kPa>  DEFLATE end pressure (EMPTY_MIN_KPA..EMPTY_MAX_KPA, e.g. v-1 for suction)
//   z  zero the pressure (bag empty, IDLE)    ?  print the settings
// Targets are capped at MAX_KPA - TARGET_MARGIN_KPA. Safety (in the firmware, whatever the PC
// does): pressure above MAX_KPA, sensor out of range, or FILL timeout -> stop with deflation.
// Printed: "F,<ms>,<phase>" at every phase change; "P,<ms>,<kPa>,<target kPa>,<pwm in>,<pwm out>,<phase>"
// every PRINT_MS (every second when IDLE); "# ..." information and faults.

#include <Arduino.h>
#include <BluetoothSerial.h>
#include <math.h>

// ---------------- Pins ----------------
// Pump 1: inflation (channel A)
const int PWMA = 25;
const int AIN1 = 27;
const int AIN2 = 26;
// Pump 2: deflation (channel B)
const int PWMB = 18;
const int BIN1 = 19;
const int BIN2 = 21;
// Shared
const int STBY = 22;
const int pressurePin = 34;  // GPIO34, ADC1 (input only)

// ---------------- Settings ----------------
const uint32_t SERIAL_BAUD = 115200;
const char *BT_NAME = "Airbag-ESP32";
const unsigned long TICK_MS = 10;              // control loop 100 Hz
const unsigned long PRINT_MS = 100;            // P lines while running
const unsigned long IDLE_PRINT_MS = 1000;      // P lines while IDLE

// Breathing timing
const unsigned long INHALE_MS = 4000;
const unsigned long EXHALE_MS = 6000;

// Breathing and pressure settings. Defaults until changed (e / i / a / d / c, or run_session.py from
// settings.py); the comfortable values are still to be found by testing.
int inhalePwm = 255;                           // (placeholder) pump 1 during INHALE
int exhalePwm = 120;                           // (placeholder) pump 2 during EXHALE (user, 2026-11-04)
float inhaleKpa = 3.0;                         // (placeholder) INHALE: pump 1 off at or above this
float exhaleKpa = -1.0;                        // (placeholder) EXHALE: pump 2 off at or below this (suction)
float holdKpa = 0.6;                           // constant mode target
bool holdMode = false;

// Safety
const float MAX_KPA = 8.0;                     // (placeholder) above: stop and deflate
const float TARGET_MARGIN_KPA = 1.0;           // targets at most MAX_KPA - this ...
const float MIN_TARGET_KPA = -2.0;             // ... and at least this (the sensor reads to about -5 kPa)
// DEFLATE ends below emptyKpa ... The sensor is specified for 0-100 kPa only: readings below 0
// are uncalibrated, and below SENSOR_MIN_V (about -5 kPa) it is out of range.
float emptyKpa = -1.0;                         // (placeholder) negative: suck the bag flat
const float EMPTY_MIN_KPA = -4.0;
const float EMPTY_MAX_KPA = 0.3;
const unsigned long DEFLATE_MAX_MS = 20000;    // ... or after this long
const unsigned long FILL_TIMEOUT_MS = 90000;   // FILL target not reached -> leak? stop
const float FILL_DONE_KPA = 0.2;               // FILL done within this of its target ...
const unsigned long FILL_HOLD_MS = 500;        // ... for this long (a single noisy reading is not enough)
const unsigned long LINK_TIMEOUT_MS = 5000;    // no heartbeat this long -> stop
// PI controller: u = Kp * error + integral, error = target - pressure (kPa).
// u > 0: pump 1, u < 0: pump 2; |u| mapped onto PWM_MIN..255.
float kp = 150;                                // (placeholder) PWM per kPa; 60 was too slow (2026-11-04 test)
float ki = 20;                                 // (placeholder) PWM per kPa*s
const float DEADBAND_KPA = 0.03;               // |error| below: integral held and still applied (makes up for leaks)
const float U_OFF = 5;                         // |u| below: off
const int PWM_MIN = 80;                        // (placeholder) lowest PWM at which a pump runs

// Pressure sensor XGZP6847A 100 kPa, 5 V supply: Vout = 0.5 V + 0.04 V/kPa
const float SENSOR_V0 = 0.5;
const float SENSOR_V_PER_KPA = 0.04;
const float PRESSURE_DIVIDER = 1.6;            // Sensor OUT -- 12k -- GPIO34 -- 20k -- GND
const float SENSOR_MIN_V = 0.3;                // outside 0.3-4.7 V: wire off / sensor fault
const float SENSOR_MAX_V = 4.7;
const int ADC_AVG_N = 8;
const float FILTER_ALPHA = 0.3;                // EMA per tick
const float ZERO_LIMIT_KPA = 3.0;              // auto-zero only if the offset is within this

// false: everything runs but the motors stay off (dry run)
const bool MOTOR_OUTPUT_ENABLED = true;

// ---------------- Links ----------------
BluetoothSerial SerialBT;

// Everything printed goes to USB and Bluetooth
class Tee : public Print {
 public:
  size_t write(uint8_t c) override {
    Serial.write(c);
    if (SerialBT.hasClient()) SerialBT.write(c);
    return 1;
  }
  size_t write(const uint8_t *buf, size_t n) override {
    Serial.write(buf, n);
    if (SerialBT.hasClient()) SerialBT.write(buf, n);
    return n;
  }
};
Tee out;

// One command line buffer per link, so the two cannot mix
struct LineBuf {
  char text[24];
  int len = 0;
};
LineBuf usbLine, btLine;

// ---------------- State ----------------
enum Phase { IDLE, FILL, INHALE, EXHALE, HOLD, DEFLATE };

Phase phase = IDLE;
unsigned long phaseStart = 0, lastTick = 0, lastPrint = 0, lastHeartbeat = 0, lastRefusal = 0;
unsigned long fillReachedAt = 0;  // 0 = FILL target not reached (continuously) yet
bool heartbeatSeen = false;bool faultLatched = false;
float zeroKpa = 0;
float pressure = 0;      // filtered, kPa
float sensorV = 0;       // last raw sensor voltage
float target = 0, integral = 0;
int pwmIn = 0, pwmOut = 0;

const char *phaseName(Phase p) {
  switch (p) {
    case FILL: return "FILL";
    case INHALE: return "INHALE";
    case EXHALE: return "EXHALE";
    case HOLD: return "HOLD";
    case DEFLATE: return "DEFLATE";    default: return "IDLE";
  }
}

// ---------------- Pumps ----------------
void setPumps(int in, int out) {
  pwmIn = MOTOR_OUTPUT_ENABLED ? constrain(in, 0, 255) : 0;
  pwmOut = MOTOR_OUTPUT_ENABLED ? constrain(out, 0, 255) : 0;
  digitalWrite(STBY, (pwmIn || pwmOut) ? HIGH : LOW);
  digitalWrite(AIN1, pwmIn ? HIGH : LOW);
  digitalWrite(AIN2, LOW);
  analogWrite(PWMA, pwmIn);
  digitalWrite(BIN1, pwmOut ? HIGH : LOW);
  digitalWrite(BIN2, LOW);
  analogWrite(PWMB, pwmOut);
}

// ---------------- Pressure ----------------
float readSensorV() {
  uint32_t sum = 0;
  for (int i = 0; i < ADC_AVG_N; i++) sum += analogReadMilliVolts(pressurePin);
  return sum / (float)ADC_AVG_N / 1000.0 * PRESSURE_DIVIDER;
}

float voltsToKpa(float v) {
  return (v - SENSOR_V0) / SENSOR_V_PER_KPA;
}

bool sensorOk() {
  return sensorV >= SENSOR_MIN_V && sensorV <= SENSOR_MAX_V;
}

void zeroPressure() {
  float sum = 0;
  for (int i = 0; i < 100; i++) {
    sum += voltsToKpa(readSensorV());
    delay(10);
  }
  float offset = sum / 100;
  if (fabs(offset) <= ZERO_LIMIT_KPA) {
    zeroKpa = offset;
    pressure = 0;
    out.printf("# zero offset %.2f kPa\n", zeroKpa);
  } else {
    out.printf("# zero NOT set: offset %.2f kPa (bag not empty or sensor fault?)\n", offset);
  }
}

// ---------------- Phases ----------------
// now: the tick's time, so the new phase's elapsed time starts at 0
void changePhase(Phase next, unsigned long now) {
  phase = next;
  phaseStart = now;
  fillReachedAt = 0;
  if (next == FILL || next == DEFLATE || next == IDLE) integral = 0;  // kept from breath to breath
  setPumps(0, 0);
  out.printf("F,%lu,%s\n", now, phaseName(phase));
}

bool running() {
  return phase != IDLE && phase != DEFLATE;
}

void fault(const char *why, unsigned long now) {
  out.printf("# FAULT: %s (p %.2f kPa, sensor %.2f V); stopping, start blocked until x\n", why, pressure, sensorV);
  faultLatched = true;
  changePhase(DEFLATE, now);
}

float capTarget(float kpa) {
  return constrain(kpa, MIN_TARGET_KPA, MAX_KPA - TARGET_MARGIN_KPA);
}

void printSettings() {
  out.printf("# mode %s | inhale pwm %d to %.2f kPa, exhale pwm %d to %.2f kPa | hold %.2f kPa | empty %.2f | max %.2f | kp %.1f ki %.1f | zero %.2f kPa | motors %s\n",
                holdMode ? "constant" : "breathing", inhalePwm, inhaleKpa, exhalePwm, exhaleKpa, holdKpa, emptyKpa, MAX_KPA,
                kp, ki, zeroKpa,
                MOTOR_OUTPUT_ENABLED ? "on" : "OFF (dry run)");
}

// ---------------- Commands ----------------
void runLine(LineBuf &line, unsigned long now) {
  line.text[line.len] = 0;
  char cmd = line.text[0];
  float v = atof(line.text + 1);
  bool hasValue = line.len > 1;
  switch (cmd) {
    case 'e':
      if (hasValue) exhaleKpa = capTarget(v);
      inhaleKpa = max(inhaleKpa, exhaleKpa);
      printSettings();
      break;
    case 'i': if (hasValue) inhaleKpa = max(capTarget(v), exhaleKpa); printSettings(); break;
    case 'c':
      if (hasValue) holdKpa = capTarget(v);
      holdMode = true;
      if (phase == INHALE || phase == EXHALE) changePhase(HOLD, now);
      printSettings();
      break;
    case 'b':
      holdMode = false;
      if (phase == HOLD) changePhase(INHALE, now);
      printSettings();
      break;
    case 'a': if (hasValue) inhalePwm = constrain((int)v, 0, 255); printSettings(); break;
    case 'd': if (hasValue) exhalePwm = constrain((int)v, 0, 255); printSettings(); break;
    case 'p': if (hasValue && v >= 0) kp = v; printSettings(); break;
    case 'n': if (hasValue && v >= 0) ki = v; printSettings(); break;
    case 'v': if (hasValue) emptyKpa = constrain(v, EMPTY_MIN_KPA, EMPTY_MAX_KPA); printSettings(); break;
    case 'z':
      if (phase == IDLE) zeroPressure(); else out.println("# zero only when IDLE");
      break;
    case '?': printSettings(); break;
    default: break;  // e.g. the PC's "C,..." lines
  }
}

void handleChar(LineBuf &line, char c, unsigned long now) {
  if (c == '\n' || c == '\r') {
    if (line.len) runLine(line, now);
    line.len = 0;
    return;
  }
  if (line.len == 0) {
    switch (c) {
      case 's':
        if (phase != IDLE) return;
        if (faultLatched || !sensorOk()) {
          if (now - lastRefusal >= 5000) {
            lastRefusal = now;
            out.println(faultLatched ? "# not starting: fault (send x first)" : "# not starting: sensor fault");
          }
          return;
        }
        changePhase(holdMode ? FILL : INHALE, now);  // breathing: no fill, the first inhale fills
        return;
      case 'x':
        faultLatched = false;
        if (running()) changePhase(DEFLATE, now);
        return;
      case 'h':
        heartbeatSeen = true;
        lastHeartbeat = now;
        return;
    }
  }
  if (line.len < (int)sizeof(line.text) - 1) line.text[line.len++] = c;
}

// ---------------- Control ----------------
// PI towards target (constant mode)
void control(float dt) {
  float error = target - pressure;
  if (fabsf(error) < DEADBAND_KPA) error = 0;  // pumps off here let the bag leak down to target - deadband
  float u = kp * error + integral;
  bool saturated = fabsf(u) >= 255;
  if (!saturated || (error > 0) != (integral > 0)) {  // anti-windup: no integrating further into saturation
    integral = constrain(integral + ki * error * dt, -255.0f, 255.0f);
  }
  u = kp * error + integral;
  if (fabsf(u) < U_OFF) {
    setPumps(0, 0);
    return;
  }
  int pwm = PWM_MIN + (int)((255 - PWM_MIN) * min(fabsf(u), 255.0f) / 255);
  if (u > 0) setPumps(pwm, 0); else setPumps(0, pwm);
}

// ---------------- Setup / loop ----------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  SerialBT.begin(BT_NAME);
  pinMode(PWMA, OUTPUT);
  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(PWMB, OUTPUT);
  pinMode(BIN1, OUTPUT);
  pinMode(BIN2, OUTPUT);
  pinMode(STBY, OUTPUT);
  pinMode(pressurePin, INPUT);
  setPumps(0, 0);

  analogReadResolution(12);
  analogSetPinAttenuation(pressurePin, ADC_11db);

  out.println();
  out.println("# ESP32 airbag pump (pressure control)");
  out.println("# s start, x stop, h heartbeat; e/i/c/v<kPa>, a/d<pwm>, b, p/n<val>, z, ? + Enter");
  sensorV = readSensorV();
  if (sensorOk()) zeroPressure();
  else out.printf("# sensor fault: %.2f V (expected about 0.5 V with the bag empty)\n", sensorV);
  printSettings();
  changePhase(IDLE, millis());
}

void loop() {
  unsigned long now = millis();
  while (Serial.available() > 0) handleChar(usbLine, Serial.read(), now);
  while (SerialBT.available() > 0) handleChar(btLine, SerialBT.read(), now);
  if (now - lastTick < TICK_MS) return;
  float dt = (now - lastTick) / 1000.0;
  lastTick = now;

  sensorV = readSensorV();
  pressure += FILTER_ALPHA * (voltsToKpa(sensorV) - zeroKpa - pressure);
  unsigned long elapsed = now - phaseStart;

  // Safety
  if (!sensorOk()) {
    if (running()) fault("sensor out of range", now);
  } else if (phase != DEFLATE && pressure > MAX_KPA) {
    fault("over pressure", now);
  }
  if (heartbeatSeen && running() && now - lastHeartbeat > LINK_TIMEOUT_MS) {
    out.println("# no heartbeat from the PC, stopping");
    changePhase(DEFLATE, now);
  }
  elapsed = now - phaseStart;

  switch (phase) {
    case FILL:
      target = holdKpa;
      if (pressure < target - FILL_DONE_KPA) fillReachedAt = 0;
      else if (!fillReachedAt) fillReachedAt = now ? now : 1;
      if (fillReachedAt && now - fillReachedAt >= FILL_HOLD_MS) changePhase(HOLD, now);
      else if (elapsed >= FILL_TIMEOUT_MS) fault("fill target not reached (leak?)", now);
      else control(dt);
      break;
    case INHALE:  // open loop; target = the stop pressure
      if (elapsed >= INHALE_MS) { changePhase(EXHALE, now); break; }
      target = inhaleKpa;
      setPumps(pressure < inhaleKpa ? inhalePwm : 0, 0);
      break;
    case EXHALE:
      if (elapsed >= EXHALE_MS) { changePhase(INHALE, now); break; }
      target = exhaleKpa;
      setPumps(0, pressure > exhaleKpa ? exhalePwm : 0);
      break;
    case HOLD:
      target = holdKpa;
      control(dt);
      break;
    case DEFLATE:
      target = 0;
      if (sensorV < SENSOR_MIN_V || (sensorOk() && pressure < emptyKpa) || elapsed >= DEFLATE_MAX_MS) changePhase(IDLE, now);
      else setPumps(0, 255);
      break;
    case IDLE:
      target = 0;
      break;
  }

  if (now - lastPrint >= (phase == IDLE ? IDLE_PRINT_MS : PRINT_MS)) {
    lastPrint = now;
    out.printf("P,%lu,%.2f,%.2f,%d,%d,%s\n", now, pressure, target, pwmIn, pwmOut, phaseName(phase));
  }
}
