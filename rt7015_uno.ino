/*
 * RT7015 模块数据采集 —— Arduino UNO 版
 * 由 ESP32-S3 版（rt7015_esp32s3.ino）移植，数据格式、心率算法与之相同
 *
 * 开发板选：Arduino Uno（"Arduino AVR Boards" 自带，无需另装）
 *
 * 接线（UNO）：
 *   RT7015 BAT  -> 5V      （UNO 的 5V 脚可以对外供电，按商家资料用 5V）
 *   RT7015 GND  -> GND
 *   RT7015 AOUT -> A0      （模拟脉搏波形）
 *   RT7015 SOUT -> A1      （方波脉冲，每次心跳一个）
 *   UNO 的模拟输入量程是 0~5V，模块用 5V 供电时输出不会超量程。
 *
 * 采样：Timer1 定时中断 500Hz，每次 A0、A1 各读 4 次取平均 -> 每通道 500Hz（与 ESP32 版一致）
 *
 * 两种输出模式（改 OUTPUT_MODE）：
 *   0 = 文本，"AOUT:xxx,SOUT:yyy,BPM:zz" 每行一组，可直接用 Arduino IDE 的串口绘图器看波形
 *   1 = 二进制帧，与商家上位机 / rt7015_viewer.py 兼容：
 *       A5 | F2 | 11 | E3 | 60组 x (AOUT u16 小端, SOUT u16 小端) | 5A
 *       另外每秒发一帧心率：A5 | 04 | 12 | 16 | BPM u16 小端 | 5A
 *
 * 心率计算：对 HR_SOURCE 指定的通道做自适应阈值检测，每次信号从低到高越过阈值记为一次心跳，
 *   心率 = 60 / 最近几次心跳间隔的中位数。BPM=0 表示没检测到稳定心跳（手指没放好、信号太弱或乱跳）。
 */

#include <Arduino.h>

// ================= 用户配置 =================
#define AOUT_PIN        A0
#define SOUT_PIN        A1
#define OUTPUT_MODE     0        // 0=文本(串口绘图器)  1=二进制帧(上位机/脚本)
#define BIN_UNIT_01MV   1        // 二进制帧数值单位：1=0.1mV(同STM32例程)  0=ADC原始值0~1023
#define PLOT_DECIMATE   5        // 文本模式每 N 个点输出一次（500/5 = 100Hz）；UNO 串口带宽有限，不建议小于 5，要看 500Hz 全部数据请用二进制模式
#define SERIAL_BAUD     115200   // 串口监视器/绘图器/上位机要选同样的波特率
#define VREF_MV         5000     // ADC 参考电压(mV)=UNO 的 5V 实际电压；用万用表量 5V 脚后填进来，读数更准
#define OVERSAMPLE      4        // 每个通道每次读几次取平均

#define HR_SOURCE       0        // 心率用哪一路：0=AOUT  1=SOUT
#define HR_MIN_AMP_MV   50       // 信号起伏小于这个值(mV)认为没有有效信号
#define HR_MIN_BPM      35       // 有效心率范围
#define HR_MAX_BPM      180
#define HR_PRINT_BEATS  0        // 1=每检测到一次心跳额外打印一行文字（会干扰串口绘图器，调试用）
// ===========================================

#define SAMPLES_PER_FRAME 60
static uint8_t  tx_frame[4 + SAMPLES_PER_FRAME * 4 + 1];
static uint8_t  sample_idx = 0;

// 定时中断 -> 主循环 的采样缓冲（约 64ms），主循环发送数据时不会丢采样
#define RB_SIZE 32
static volatile uint16_t rb_a[RB_SIZE], rb_s[RB_SIZE];
static volatile uint8_t  rb_head = 0, rb_tail = 0;

// 显式声明函数原型，避免 IDE 自动生成原型时出错
void pushBinary(uint16_t a, uint16_t s);
void sendBpmFrame(uint16_t bpm);
bool hrProcess(float x_mv);
int  hrGetBpm();
void setup();
void loop();

// ==== HR BEGIN ==== 心率检测（纯计算，不依赖硬件）
#define HR_FS 500                          // 每通道采样率
static float    hr_y = 0, hr_max = 0, hr_min = 0;
static bool     hr_init = false, hr_armed = false;
static uint32_t hr_n = 0, hr_last_beat = 0;
static uint32_t hr_iv[8];                  // 最近的心跳间隔（采样点数）
static uint8_t  hr_iv_cnt = 0, hr_iv_pos = 0;
static int      hr_bpm = 0;
static float    hr_box[10];                // 10 点(20ms)滑动平均：正好滤掉 50Hz 工频干扰
static float    hr_box_sum = 0;
static uint8_t  hr_box_pos = 0;
static uint16_t hr_lo_cnt = 0, hr_hi_cnt = 0;
static uint32_t hr_cross_n = 0;
#define HR_MIN_WIDTH (HR_FS * 40 / 1000)   // 高/低电平至少保持 40ms 才算数，过滤抖动和毛刺

// 输入一个采样点（mV），检测到心跳时返回 true
bool hrProcess(float x_mv) {
  hr_n++;
  if (!hr_init) {
    for (int i = 0; i < 10; i++) hr_box[i] = x_mv;
    hr_box_sum = 10 * x_mv;
    hr_y = hr_max = hr_min = x_mv; hr_init = true; return false;
  }
  hr_box_sum += x_mv - hr_box[hr_box_pos];
  hr_box[hr_box_pos] = x_mv;
  hr_box_pos = (hr_box_pos + 1) % 10;
  hr_y = hr_box_sum / 10;

  // 包络跟踪：峰/谷值缓慢向中间回收（约 2 秒），适应基线漂移
  float range = hr_max - hr_min;
  float decay = range / (2.0f * HR_FS);
  if (hr_y > hr_max) hr_max = hr_y; else hr_max -= decay;
  if (hr_y < hr_min) hr_min = hr_y; else hr_min += decay;
  range = hr_max - hr_min;

  bool beat = false;
  uint32_t since = hr_n - hr_last_beat;
  if (range >= HR_MIN_AMP_MV) {
    float hi = hr_min + 0.6f * range;      // 带回差的双阈值，防止在阈值附近来回抖动
    float lo = hr_min + 0.4f * range;
    if (hr_y < lo) { if (hr_lo_cnt < 60000) hr_lo_cnt++; } else hr_lo_cnt = 0;
    if (hr_lo_cnt >= HR_MIN_WIDTH) hr_armed = true;
    if (hr_y > hi) { if (hr_hi_cnt == 0) hr_cross_n = hr_n; if (hr_hi_cnt < 60000) hr_hi_cnt++; } else hr_hi_cnt = 0;
    since = hr_cross_n - hr_last_beat;
    if (hr_armed && hr_hi_cnt >= HR_MIN_WIDTH && since > (uint32_t)(HR_FS * 60 / HR_MAX_BPM)) {
      hr_armed = false;
      beat = true;
      if (hr_last_beat != 0 && since <= (uint32_t)(HR_FS * 60 / HR_MIN_BPM)) {
        hr_iv[hr_iv_pos] = since;
        hr_iv_pos = (hr_iv_pos + 1) % 8;
        if (hr_iv_cnt < 8) hr_iv_cnt++;
      } else {
        hr_iv_cnt = 0;                     // 间隔不合理：重新开始统计
      }
      hr_last_beat = hr_cross_n;           // 心跳时刻取越过阈值的那一刻
    }
  }

  // 超过最长间隔没心跳 -> 清零
  if (hr_n - hr_last_beat > (uint32_t)(HR_FS * 60 / HR_MIN_BPM)) { hr_iv_cnt = 0; hr_bpm = 0; }

  if (beat) {
    if (hr_iv_cnt >= 3) {                  // 至少 3 个间隔才给出心率，取最近 5 个的中位数
      uint8_t k = hr_iv_cnt < 5 ? hr_iv_cnt : 5;
      uint32_t v[5];
      for (uint8_t i = 0; i < k; i++) v[i] = hr_iv[(hr_iv_pos + 8 - 1 - i) % 8];
      for (uint8_t i = 1; i < k; i++) {    // 插入排序
        uint32_t t = v[i]; int j = i - 1;
        while (j >= 0 && v[j] > t) { v[j + 1] = v[j]; j--; }
        v[j + 1] = t;
      }
      // 间隔忽长忽短（最长 > 最短的 1.5 倍）说明是干扰或手在动，不给结果
      if (v[k - 1] * 2 > v[0] * 3) hr_bpm = 0;
      else hr_bpm = (int)(60.0f * HR_FS / v[k / 2] + 0.5f);
    } else {
      hr_bpm = 0;
    }
  }
  return beat;
}

int hrGetBpm() { return hr_bpm; }
// ==== HR END ====

// 500Hz 定时中断：读 A0、A1 各 OVERSAMPLE 次，存入缓冲
ISR(TIMER1_COMPA_vect) {
  uint16_t a = 0, s = 0;
  for (uint8_t i = 0; i < OVERSAMPLE; i++) a += analogRead(AOUT_PIN);
  for (uint8_t i = 0; i < OVERSAMPLE; i++) s += analogRead(SOUT_PIN);
  uint8_t next = (rb_head + 1) % RB_SIZE;
  if (next != rb_tail) {          // 缓冲满了就丢弃这一点（正常不会发生）
    rb_a[rb_head] = a;
    rb_s[rb_head] = s;
    rb_head = next;
  }
}

void pushBinary(uint16_t a, uint16_t s) {
  uint8_t *p = &tx_frame[4 + sample_idx * 4];
  p[0] = a & 0xFF; p[1] = a >> 8;
  p[2] = s & 0xFF; p[3] = s >> 8;
  if (++sample_idx >= SAMPLES_PER_FRAME) {
    tx_frame[0] = 0xA5;
    tx_frame[1] = 2 + SAMPLES_PER_FRAME * 4;   // 242
    tx_frame[2] = 0x11;
    tx_frame[3] = tx_frame[1] ^ tx_frame[2];
    tx_frame[sizeof(tx_frame) - 1] = 0x5A;
    Serial.write(tx_frame, sizeof(tx_frame));
    sample_idx = 0;
  }
}

void sendBpmFrame(uint16_t bpm) {
  uint8_t f[7] = {0xA5, 0x04, 0x12, 0x04 ^ 0x12, (uint8_t)(bpm & 0xFF), (uint8_t)(bpm >> 8), 0x5A};
  Serial.write(f, sizeof(f));
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  analogReference(DEFAULT);       // 参考电压 = 5V
  analogRead(AOUT_PIN);           // 先读一次让 ADC 稳定
  analogRead(SOUT_PIN);

#if OUTPUT_MODE == 0
  Serial.println(F("RT7015 start (text mode)"));
#endif

  // Timer1：CTC 模式，16MHz / 64 / 500 = 500Hz
  noInterrupts();
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  TCNT1  = 0;
  OCR1A  = 499;
  TIMSK1 = _BV(OCIE1A);
  interrupts();
}

void loop() {
  while (true) {
    // 从缓冲取一个采样点
    noInterrupts();
    if (rb_tail == rb_head) { interrupts(); break; }
    uint16_t a_sum = rb_a[rb_tail], s_sum = rb_s[rb_tail];
    rb_tail = (rb_tail + 1) % RB_SIZE;
    interrupts();

    uint16_t a_raw = a_sum / OVERSAMPLE, s_raw = s_sum / OVERSAMPLE;   // 0~1023
    int a_mv = (int)((uint32_t)a_sum * VREF_MV / (1023UL * OVERSAMPLE));
    int s_mv = (int)((uint32_t)s_sum * VREF_MV / (1023UL * OVERSAMPLE));
    (void)a_raw; (void)s_raw;

    // ---- 心率检测（每个 500Hz 采样点都处理）----
    bool beat = hrProcess(HR_SOURCE == 0 ? (float)a_mv : (float)s_mv);
    int bpm = hrGetBpm();
#if HR_PRINT_BEATS && OUTPUT_MODE == 0
    if (beat) { Serial.print(F("beat, BPM=")); Serial.println(bpm); }
#endif
    (void)beat;

#if OUTPUT_MODE == 0
    static uint8_t decim_cnt = 0;
    if (++decim_cnt >= PLOT_DECIMATE) {
      decim_cnt = 0;
      Serial.print(F("AOUT:")); Serial.print(a_mv);        // 电压单位 mV
      Serial.print(F(",SOUT:")); Serial.print(s_mv);
      Serial.print(F(",BPM:"));  Serial.println(bpm);
    }
#else
  #if BIN_UNIT_01MV
    pushBinary((uint16_t)((uint32_t)a_mv * 10), (uint16_t)((uint32_t)s_mv * 10));
  #else
    pushBinary(a_raw, s_raw);
  #endif
    static uint16_t bpm_cnt = 0;
    if (++bpm_cnt >= 500) { bpm_cnt = 0; sendBpmFrame((uint16_t)bpm); }   // 每秒一次
#endif
  }
}
