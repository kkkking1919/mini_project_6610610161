#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_HTS221.h>
#include <Adafruit_BMP280.h>
#include <Adafruit_MPU6050.h>

// Cucumber RS On-board I2C Pins (ESP32-S2)
#define I2C_SDA 41
#define I2C_SCL 40

// WiFi Configuration
const char* ssid = "Double19";
const char* password = "19191919";

// FastAPI Server Endpoints:
// 1. Direct LAN IP (Ultra-low latency, works offline without internet when port 8080 is accessible)
const char* serverUrl_lan_telemetry    = "http://172.20.10.5:8080/api/sensor-data";
const char* serverUrl_lan_fall         = "http://172.20.10.5:8080/api/fall-event";

// 2. Cloudflare Public Tunnel (Auto-fallback: bypasses Windows Firewall / NAT and works across any network)
const char* serverUrl_tunnel_telemetry = "https://proposed-border-following-union.trycloudflare.com/api/sensor-data";
const char* serverUrl_tunnel_fall      = "https://proposed-border-following-union.trycloudflare.com/api/fall-event";

// เซนเซอร์ออนบอร์ด
Adafruit_HTS221 hts;
Adafruit_BMP280 bmp(&Wire);
Adafruit_MPU6050 mpu;

bool hts_found = false;
bool bmp_found = false;
bool mpu_found = false;

// FreeRTOS Mutex สำหรับป้องกันการชนกันของ I2C Bus ระหว่าง Tasks
SemaphoreHandle_t i2cMutex = NULL;

// --- EDGE-AI FALL DETECTION (HIGH-SENSITIVITY 3-STAGE STATE MACHINE) ---
enum FallState {
  STATE_MONITORING,
  STATE_FREEFALL,
  STATE_IMPACT,
  STATE_INACTIVITY
};

FallState fallState = STATE_MONITORING;
unsigned long freefallStartTime = 0;
unsigned long impactStartTime = 0;
unsigned long lastFallTriggerTime = 0;
float maxImpactG = 0.0;

// เกณฑ์การตรวจจับความไวสูง (High-Sensitivity Thresholds ตามที่กำหนดร่วมกัน)
const float FREEFALL_THRESHOLD = 0.70;      // < 0.70 G (ทิ้งตัวไร้น้ำหนัก ตรวจจับได้ง่ายขึ้น)
const float IMPACT_THRESHOLD   = 2.00;      // > 2.00 G (แรงกระแทกพื้น ตรวจจับการตกบนเบาะได้ดีขึ้น)
const unsigned long FREEFALL_WINDOW = 750;  // หน้าต่างเวลาเกิดแรงกระแทกหลังทิ้งตัว (750ms)
const unsigned long INACTIVITY_TIME = 500;  // เวลารอนิ่งเหลือเพียง 500ms (0.5 วินาที ตอบสนองไวมาก)
const unsigned long FALL_COOLDOWN   = 4000; // Cooldown 4 วินาที ป้องกันส่งซ้ำ

// ตัวแปรส่งผ่านสถานะระหว่าง Task สุ่มค่าและ Task ส่งข้อมูล
volatile bool fallPending = false;
volatile float pendingMaxG = 0.0;

// ตัวแปรเก็บค่าเซนเซอร์ล่าสุด
float current_temp = 0.0;
float current_hum = 0.0;
float current_press = 0.0;
sensors_event_t current_a, current_g, current_temp_mpu;

// ตัวจับเวลาสำหรับ Routine Telemetry
unsigned long lastTelemetryTime = 0;
const unsigned long TELEMETRY_INTERVAL = 1000; // ส่ง Telemetry ทุก 1 วินาที (near real-time)

// --- MOTION SUMMARY: สรุปการเคลื่อนไหวทุก sample (50Hz) ระหว่างรอบส่ง Telemetry ---
// Task ตรวจจับการล้มเขียน, loop() อ่านแล้วรีเซ็ต -> ป้องกันด้วย critical section
portMUX_TYPE motionMux = portMUX_INITIALIZER_UNLOCKED;
float motion_g_min = 99.0, motion_g_max = 0.0, motion_g_sum = 0.0;
float motion_rot_max = 0.0, motion_rot_sum = 0.0;
uint32_t motion_samples = 0;

// --- NETWORK: เส้นทางส่งข้อมูล LAN / Cloudflare Tunnel ---
// ใช้ client แบบ global + HTTP keep-alive เพื่อไม่ต้องเปิด TCP/TLS ใหม่ทุกครั้ง
// (TLS handshake บน ESP32-S2 ใช้เวลา ~1-2 วินาทีต่อครั้ง)
WiFiClient lanClient;
WiFiClientSecure tunnelClient;
HTTPClient lanHttp;
HTTPClient tunnelHttp;

// จำว่า LAN ใช้ไม่ได้ แล้วข้ามไปใช้ Tunnel ตรงๆ จนถึงเวลาลองใหม่ (ไม่เสียเวลารอ LAN timeout ทุกรอบ)
const unsigned long LAN_RETRY_INTERVAL = 60000; // ลอง LAN ใหม่ทุก 60 วินาที
const uint16_t LAN_CONNECT_TIMEOUT_MS = 800;
unsigned long lanRetryAt = 0;        // 0 = ลอง LAN ได้ทันที
bool lanAvailable = true;

// ฟังก์ชันแปลงระดับความแรงสัญญาณ RSSI
String getSignalQuality(int rssi) {
  if (rssi >= -50) return "Excellent (ดีมาก)";
  if (rssi >= -60) return "Good (ดี)";
  if (rssi >= -70) return "Fair (ปานกลาง)";
  if (rssi >= -80) return "Weak (อ่อน)";
  return "Very Weak (อ่อนมาก)";
}

// ส่ง HTTP POST ผ่าน HTTPClient/Client ที่ถือไว้ตลอด (keep-alive: ใช้ connection เดิมซ้ำถ้ายังเปิดอยู่)
bool postJson(HTTPClient& http, WiFiClient& client, const char* url, const String& payload,
              uint16_t connectTimeoutMs, uint16_t timeoutMs) {
  if (url == NULL || strlen(url) == 0) return false;
  unsigned long t0 = millis();

  http.setReuse(true);
  if (!http.begin(client, url)) {
    Serial.printf("[HTTP] begin() failed: %s\n", url);
    return false;
  }
  http.setConnectTimeout(connectTimeoutMs);
  http.setTimeout(timeoutMs);
  http.addHeader("Content-Type", "application/json");

  int code = http.POST(payload);
  bool success = (code > 0 && code < 400);
  if (success) {
    Serial.printf("[HTTP %d OK] %lums %s\n", code, millis() - t0, url);
    http.end();            // with reuse=true the TCP/TLS connection stays open
  } else {
    Serial.printf("[HTTP Failed] %s -> %s (Code: %d, %lums)\n", url, http.errorToString(code).c_str(), code, millis() - t0);
    http.end();
    client.stop();         // drop a possibly stale keep-alive connection; next call reconnects
  }
  return success;
}

bool postLan(const char* url, const String& payload) {
  return postJson(lanHttp, lanClient, url, payload, LAN_CONNECT_TIMEOUT_MS, 1500);
}

bool postTunnel(const char* url, const String& payload) {
  return postJson(tunnelHttp, tunnelClient, url, payload, 5000, 6000);
}

// ส่งแบบ Dual-Route ที่จำเส้นทาง: ถ้า LAN เพิ่งล้มเหลว ให้ไป Tunnel ตรงๆ จนกว่าจะถึงเวลาลอง LAN ใหม่
// alwaysTryBoth = true (ใช้กับ Fall Alert): ถ้าเส้นทางแรกล้มเหลว จะลองอีกเส้นทางเสมอ
bool sendDualRoute(const char* lanUrl, const char* tunnelUrl, const String& payload, bool alwaysTryBoth) {
  unsigned long now = millis();
  bool tryLanFirst = lanAvailable || (long)(now - lanRetryAt) >= 0;

  if (tryLanFirst) {
    if (postLan(lanUrl, payload)) {
      if (!lanAvailable) Serial.println("[ROUTE] LAN is reachable again -> using direct LAN route");
      lanAvailable = true;
      return true;
    }
    lanAvailable = false;
    lanRetryAt = millis() + LAN_RETRY_INTERVAL;
    Serial.printf("[ROUTE] LAN unreachable -> using Cloudflare Tunnel for the next %lus\n", LAN_RETRY_INTERVAL / 1000);
    return strlen(tunnelUrl) > 0 && postTunnel(tunnelUrl, payload);
  }

  if (strlen(tunnelUrl) > 0 && postTunnel(tunnelUrl, payload)) return true;
  return alwaysTryBoth && postLan(lanUrl, payload);
}

// ฟังก์ชันส่งสัญญาณเตือนภัยฉุกเฉิน (Fall Event + Context) ไปยังเซิร์ฟเวอร์ (LAN -> Cloudflare Fallback)
void dispatchFallAlert(float peak_g) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[-] WiFi not connected, cannot dispatch fall alert.");
    return;
  }

  // อ่านบริบทสภาพแวดล้อม ณ เสี้ยววินาทีที่เกิดเหตุอย่างปลอดภัยด้วย Mutex
  if (xSemaphoreTake(i2cMutex, pdMS_TO_TICKS(60)) == pdTRUE) {
    if (hts_found) {
      sensors_event_t h, t;
      hts.getEvent(&h, &t);
      current_temp = t.temperature;
      current_hum = h.relative_humidity;
    }
    if (bmp_found) {
      current_press = bmp.readPressure() / 100.0F;
    }
    xSemaphoreGive(i2cMutex);
  }

  Serial.println("\n*******************************************************");
  Serial.println("🚨🚨 [REALTIME ALERT] FALL DETECTED ON EDGE (ESP32-S2)!");
  Serial.printf("   - Max Impact G-Force : %.2f G\n", peak_g);
  Serial.printf("   - Context Temp       : %.2f °C\n", current_temp);
  Serial.printf("   - Context Humidity   : %.2f %%RH\n", current_hum);
  Serial.printf("   - Context Pressure   : %.2f hPa\n", current_press);
  Serial.println("*******************************************************\n");

  JsonDocument doc;
  doc["max_g"] = peak_g;
  doc["temperature"] = current_temp;
  doc["humidity"] = current_hum;
  doc["pressure"] = current_press;

  String payload;
  serializeJson(doc, payload);

  // ส่งด้วยระบบ Dual-Route (ใช้เส้นทางที่ใช้งานได้ล่าสุดก่อน และลองอีกเส้นทางเสมอหากล้มเหลว)
  if (!sendDualRoute(serverUrl_lan_fall, serverUrl_tunnel_fall, payload, true)) {
    Serial.println("[ALERT] ❌ Fall alert could not be delivered on either route!");
  }
}

// ฟังก์ชันส่งข้อมูลสภาพแวดล้อมประจำรอบ (Routine Telemetry)
void sendRoutineTelemetry() {
  if (WiFi.status() != WL_CONNECTED) return;

  // อ่านค่าสภาพแวดล้อมอย่างปลอดภัย
  if (xSemaphoreTake(i2cMutex, pdMS_TO_TICKS(60)) == pdTRUE) {
    if (hts_found) {
      sensors_event_t hum_ev, temp_ev;
      hts.getEvent(&hum_ev, &temp_ev);
      current_temp = temp_ev.temperature;
      current_hum = hum_ev.relative_humidity;
    }
    if (bmp_found) {
      current_press = bmp.readPressure() / 100.0F;
    }
    xSemaphoreGive(i2cMutex);
  }

  int current_rssi = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : -100;

  // อ่านสรุปการเคลื่อนไหวของทุก sample (50Hz) ตั้งแต่ส่งครั้งก่อน แล้วรีเซ็ตสำหรับรอบถัดไป
  float g_min, g_max, g_sum, rot_max, rot_sum;
  uint32_t samples;
  portENTER_CRITICAL(&motionMux);
  g_min = motion_g_min; g_max = motion_g_max; g_sum = motion_g_sum;
  rot_max = motion_rot_max; rot_sum = motion_rot_sum; samples = motion_samples;
  motion_g_min = 99.0; motion_g_max = 0.0; motion_g_sum = 0.0;
  motion_rot_max = 0.0; motion_rot_sum = 0.0; motion_samples = 0;
  portEXIT_CRITICAL(&motionMux);

  // Log แบบบรรทัดเดียว (ส่งทุก 1 วินาที จึงไม่พิมพ์ยาวเกินไป)
  Serial.printf("[Telemetry] T=%.2f°C H=%.2f%% P=%.2fhPa RSSI=%d | g avg=%.2f min=%.2f max=%.2f rot max=%.2f (n=%u) | route=%s\n",
                current_temp, current_hum, current_press, current_rssi,
                samples ? g_sum / samples : 0.0f, samples ? g_min : 0.0f, g_max,
                rot_max, samples, lanAvailable ? "LAN" : "TUNNEL");

  JsonDocument doc;
  if (hts_found) {
    doc["temperature"] = current_temp;
    doc["humidity"] = current_hum;
  }
  if (bmp_found) {
    doc["pressure"] = current_press;
  }
  if (mpu_found) {
    JsonObject accel = doc["accel"].to<JsonObject>();
    accel["x"] = current_a.acceleration.x;
    accel["y"] = current_a.acceleration.y;
    accel["z"] = current_a.acceleration.z;

    JsonObject gyro = doc["gyro"].to<JsonObject>();
    gyro["x"] = current_g.gyro.x;
    gyro["y"] = current_g.gyro.y;
    gyro["z"] = current_g.gyro.z;

    if (samples > 0) {
      JsonObject motion = doc["motion"].to<JsonObject>();
      motion["g_avg"] = g_sum / samples;
      motion["g_min"] = g_min;
      motion["g_max"] = g_max;
      motion["rot_avg"] = rot_sum / samples;
      motion["rot_max"] = rot_max;
      motion["samples"] = samples;
    }
  }
  doc["rssi"] = current_rssi;

  String payload;
  serializeJson(doc, payload);

  // ส่งข้อมูลด้วยระบบ Dual-Route ที่จำเส้นทาง (LAN ตรง หรือ Cloudflare Tunnel)
  sendDualRoute(serverUrl_lan_telemetry, serverUrl_tunnel_telemetry, payload, false);
}

// --- FREERTOS TASK: HIGH-PRIORITY 50Hz FALL DETECTION (RUNS CONTINUOUSLY) ---
void fallDetectionTask(void* pvParameters) {
  TickType_t xLastWakeTime = xTaskGetTickCount();
  const TickType_t xFrequency = pdMS_TO_TICKS(20); // 20ms = 50Hz Sampling Rate

  for (;;) {
    vTaskDelayUntil(&xLastWakeTime, xFrequency);

    if (!mpu_found) continue;

    sensors_event_t a, g, temp_m;
    bool readSuccess = false;

    // อ่าน MPU-6050 โดยขอ Mutex ชั่วคราว (ใช้เวลาเพียง ~1ms)
    if (xSemaphoreTake(i2cMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
      mpu.getEvent(&a, &g, &temp_m);
      current_a = a;
      current_g = g;
      xSemaphoreGive(i2cMutex);
      readSuccess = true;
    }

    if (!readSuccess) continue;

    // คำนวณความเร่งในหน่วย G (1G = 9.80665 m/s²)
    float gx = a.acceleration.x / 9.80665;
    float gy = a.acceleration.y / 9.80665;
    float gz = a.acceleration.z / 9.80665;
    float svm = sqrt(gx * gx + gy * gy + gz * gz); // Signal Vector Magnitude
    float rot = sqrt(g.gyro.x * g.gyro.x + g.gyro.y * g.gyro.y + g.gyro.z * g.gyro.z);

    // สะสมสรุปการเคลื่อนไหวสำหรับรอบส่ง Telemetry ถัดไป (เพื่อไม่ให้พลาดการเคลื่อนไหวระหว่างรอบ)
    portENTER_CRITICAL(&motionMux);
    if (svm < motion_g_min) motion_g_min = svm;
    if (svm > motion_g_max) motion_g_max = svm;
    motion_g_sum += svm;
    if (rot > motion_rot_max) motion_rot_max = rot;
    motion_rot_sum += rot;
    motion_samples++;
    portEXIT_CRITICAL(&motionMux);

    unsigned long now = millis();

    switch (fallState) {
      case STATE_MONITORING:
        // STAGE 1: ตรวจจับสภาวะ Free-fall (ทิ้งตัวไร้น้ำหนัก < 0.70G)
        if (svm < FREEFALL_THRESHOLD) {
          freefallStartTime = now;
          maxImpactG = 0.0;
          fallState = STATE_FREEFALL;
        }
        break;

      case STATE_FREEFALL:
        // STAGE 2: ตรวจจับแรงกระแทก (Impact Spike > 2.00G) ภายใน 750ms
        if (svm > IMPACT_THRESHOLD) {
          maxImpactG = svm;
          impactStartTime = now;
          fallState = STATE_IMPACT;
        } else if (now - freefallStartTime > FREEFALL_WINDOW) {
          fallState = STATE_MONITORING; // พ้นหน้าต่างเวลา = คืนสู่สถานะปกติ
        }
        break;

      case STATE_IMPACT:
        // บันทึกแรงกระแทกจุดสูงสุด
        if (svm > maxImpactG) maxImpactG = svm;

        // รอพ้นช่วงกระแทก (300ms) แล้วเข้าสู่สภาวะ Inactivity
        if (now - impactStartTime >= 300) {
          fallState = STATE_INACTIVITY;
        }
        break;

      case STATE_INACTIVITY:
        if (svm > maxImpactG) maxImpactG = svm;

        // STAGE 3: ตรวจสอบความนิ่งหลังตกกระทบ (500ms = 0.5s รวดเร็วมาก)
        if (now - impactStartTime >= INACTIVITY_TIME) {
          // ตรวจสอบความนิ่ง (SVM เข้าสู่สภาวะสงบใกล้ 1G ไม่มีการเหวี่ยงรุนแรง)
          bool isStill = abs(svm - 1.0) < 0.50;

          if (isStill && (now - lastFallTriggerTime > FALL_COOLDOWN)) {
            lastFallTriggerTime = now;
            pendingMaxG = maxImpactG;
            fallPending = true; // ส่งสัญญาณให้ Loop ส่ง HTTP Alert ทันที!
            Serial.printf("\n🚨 [EDGE TRIGGER] Fall Confirmed! Peak Impact: %.2f G -> Queued for Realtime Alert\n", maxImpactG);
          }
          fallState = STATE_MONITORING;
        }
        break;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=======================================================");
  Serial.println(" Cucumber RS - Real-Time Edge-AI Fall Detection Node   ");
  Serial.println("=======================================================");

  // สร้าง FreeRTOS Mutex สำหรับ I2C Bus
  i2cMutex = xSemaphoreCreateMutex();

  // กำหนดขา I2C ของบอร์ด Cucumber RS (SDA = GPIO 41, SCL = GPIO 40)
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(50); // Timeout 50ms ป้องกันบอร์ดค้างจาก I2C Bus

  // 1. เริ่มต้นเซนเซอร์ HTS221 (อุณหภูมิและความชื้น) Address: 0x5F
  if (hts.begin_I2C(0x5F, &Wire)) {
    Serial.println("[+] HTS221 initialized successfully.");
    hts_found = true;
  } else {
    Serial.println("[-] Failed to find HTS221 sensor!");
  }

  // 2. เริ่มต้นเซนเซอร์ BMP280 (ความกดอากาศ) Address: 0x76 (หรือ 0x77)
  if (bmp.begin(0x76) || bmp.begin(0x77)) {
    Serial.println("[+] BMP280 initialized successfully.");
    bmp_found = true;
    bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                    Adafruit_BMP280::SAMPLING_X2,
                    Adafruit_BMP280::SAMPLING_X16,
                    Adafruit_BMP280::FILTER_X16,
                    Adafruit_BMP280::STANDBY_MS_500);
  } else {
    Serial.println("[-] Failed to find BMP280 sensor!");
  }

  // 3. ปลุกและเริ่มต้นเซนเซอร์ MPU-6050 (ความเร่งและไจโรสโคป) Address: 0x68
  Wire.beginTransmission(0x68);
  Wire.write(0x6B); // PWR_MGMT_1
  Wire.write(0x00); // Wake up
  Wire.endTransmission();

  if (mpu.begin(0x68, &Wire)) {
    Serial.println("[+] MPU-6050 initialized successfully.");
    mpu_found = true;
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_44_HZ); // เพิ่ม Bandwidth เป็น 44Hz สำหรับ 50Hz Sampling
  } else {
    Serial.println("[-] Failed to initialize MPU-6050!");
  }

  // 4. เริ่มต้นระบบ Wi-Fi
  WiFi.mode(WIFI_STA);
  if (strlen(ssid) > 0 && strcmp(ssid, "YOUR_WIFI_SSID") != 0) {
    Serial.printf("[*] Connecting to WiFi: %s\n", ssid);
    WiFi.begin(ssid, password);
    int retry = 0;
    while (WiFi.status() != WL_CONNECTED && retry < 20) {
      delay(500);
      Serial.print(".");
      retry++;
    }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("[+] WiFi Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
      Serial.println("[-] WiFi connection timeout.");
    }
  }

  // Cloudflare Tunnel ใช้ HTTPS: ข้ามการตรวจ certificate (เหมือนเดิม) และถือ connection ไว้ใช้ซ้ำ
  tunnelClient.setInsecure();

  // 5. สร้าง FreeRTOS High-Priority Fall Detection Task (50Hz / Core 0)
  xTaskCreatePinnedToCore(
    fallDetectionTask,
    "FallDetect50Hz",
    4096,
    NULL,
    2, // Priority 2 (สูงกว่า loop task ซึ่งเป็น Priority 1)
    NULL,
    0
  );

  Serial.println("[*] High-Priority 50Hz Fall Detection Task running in background...");
  Serial.println("[*] Real-Time Edge-AI Ready!\n");
}

void loop() {
  unsigned long now = millis();

  // PRIORITY 1: ตรวจพบเหตุการณ์การล้ม -> ส่งสัญญาณ Alert ทันทีโดยไม่รอรอบเวลา!
  if (fallPending) {
    float peak = pendingMaxG;
    fallPending = false; // เคลียร์สถานะ
    dispatchFallAlert(peak);
    lastTelemetryTime = millis(); // รีเซ็ตตัวจับเวลา Telemetry เพื่อหลีกเลี่ยงการส่งชนกัน
  }

  // PRIORITY 2: ส่งข้อมูล Telemetry ประจำรอบ (ทุก 1 วินาที)
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    lastTelemetryTime = now;
    sendRoutineTelemetry();
  }

  delay(20); // Yield CPU ให้ FreeRTOS scheduler
}
