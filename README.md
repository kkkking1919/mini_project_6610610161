# 🥒 Cucumber RS - Edge-AIoT Fall Detection & Healthcare Learning Kit

[![Target Audience: Students](https://img.shields.io/badge/Target%20Audience-University%20Students-blue?style=for-the-badge&logo=googlescholar)](https://github.com)
[![IoT Board: ESP32-S2](https://img.shields.io/badge/IoT%20Board-Cucumber%20RS%20(ESP32--S2)-green?style=for-the-badge&logo=espressif)](https://platformio.org/)
[![Backend: FastAPI](https://img.shields.io/badge/Backend-FastAPI%20%2B%20Docker-teal?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![AI: Google Gemini](https://img.shields.io/badge/AI%20Model-Google%20Gemini%202.5%20Flash-orange?style=for-the-badge&logo=google)](https://ai.google.dev/)
[![UI: Telegram Mini App](https://img.shields.io/badge/UI-Telegram%20Mini%20App%20%26%20Dashboard-blue?style=for-the-badge&logo=telegram)](https://telegram.org/)

ชุดทดลองต้นแบบและสื่อการเรียนรู้เทคโนโลยี **AIoT (Artificial Intelligence of Things)** พัฒนาขึ้นสำหรับ **กลุ่มเป้าหมายนักศึกษา (University Students)** สาขาวิศวกรรมคอมพิวเตอร์และเทคโนโลยีสารสนเทศ เพื่อศึกษาการผสานการทำงานระหว่างอุปกรณ์ฮาร์ดแวร์ฝังตัว (Embedded System), การสื่อสารผ่านคลาวด์แบบปลอดภัย, และการนำโมเดลภาษาขนาดใหญ่ (LLM) มาวิเคราะห์ข้อมูลสุขภาพแบบ Context-Aware

---

## 🎯 กลุ่มเป้าหมายและวัตถุประสงค์ (Target Audience)

### กลุ่มเป้าหมาย: นักศึกษา (Students / Academic Researchers)
โปรเจกต์นี้ถูกออกแบบเป็น **"ชุดการเรียนรู้ต้นแบบ (Educational Prototype)"** สำหรับการเรียนการสอนในระดับอุดมศึกษา เนื่องจากระบบยังไม่ได้ถูกออกแบบให้ทนทานต่อสภาพแวดล้อมจริง (เช่น ยังไม่มีเคสกันน้ำระดับ IP67 และยังใช้การต่อไฟตรงแทนแบตเตอรี่แบบสวมใส่) เหมาะสำหรับเป็นกรณีศึกษาในวิชา:
1. **IoT & Embedded Systems Programming:** การควบคุมไมโครคอนโทรลเลอร์ ESP32-S2 และเซนเซอร์ I2C หลายตัวพร้อมกันด้วย FreeRTOS
2. **Cloud Backend & Microservices:** การพัฒนา RESTful API ด้วย FastAPI, ฐานข้อมูล SQLite และ Docker Container
3. **Conversational AI & LLM:** การประยุกต์ใช้ Google Gemini AI นำค่าเซนเซอร์สดมาประมวลผลคำตอบแบบ Real-time
4. **Mobile & Chatbot Interface:** การพัฒนา Telegram Bot พร้อมเปิดหน้าเว็บผ่าน Telegram Mini App (TMA)

---

## 🏗️ สถาปัตยกรรมระบบ (System Architecture)

ระบบประกอบด้วย 4 ส่วนหลักที่ทำงานร่วมกันแบบครบวงจร:

```
[ บอร์ด Cucumber RS (ESP32-S2) ]
   ├── เซนเซอร์ความเร่ง 6 แกน: MPU-6050 (50Hz Fall Detection State Machine)
   ├── เซนเซอร์อุณหภูมิ/ความชื้น: HTS221
   └── เซนเซอร์ความกดอากาศ: BMP280
            │
            ▼ (HTTP / HTTPS POST)
[ Cloudflare Tunnel (พอร์ต 443 HTTPS) ]
   └── ทะลุ Mobile Hotspot AP Isolation และ Windows Firewall
            │
            ▼
[ FastAPI Server (Docker Container บน PC) ]
   ├── ตรวจจับ Context-Aware Risk (อุณหภูมิ, ความชื้น, แรงกระแทก G)
   ├── จัดเก็บฐานข้อมูล SQLite (fall_events, telemetry, daily_summaries)
   └── เชื่อมต่อ Google Gemini 2.5 Flash API
            │
            ▼
[ User Interfaces (UI) ]
   ├── 💻 Web Dashboard (Tailwind CSS + Chart.js กราฟิกแจ้งเตือนฉุกเฉิน)
   └── 📱 Telegram Bot (@bercuncu_bot) พร้อมปุ่มเปิด Telegram Mini App
```

---

## 🔌 ฮาร์ดแวร์และการต่อวงจร (Hardware Specifications)

- **บอร์ดหลัก:** Cucumber RS (Espressif ESP32-S2-Saola-1)
- **ขา I2C Bus:**
  - `I2C_SDA` : GPIO 41
  - `I2C_SCL` : GPIO 40
- **เซนเซอร์ที่ใช้งาน:**
  - `MPU-6050` (I2C: 0x68): ตรวจจับความเร่ง 3 แกน และ Gyroscope สำหรับ State Machine ตรวจการล้ม 50Hz
  - `HTS221` (I2C: 0x5F): วัดอุณหภูมิและความชื้นสัมพัทธ์ในห้อง
  - `BMP280` (I2C: 0x76/0x77): วัดความกดอากาศ

---

## 🚀 ขั้นตอนการติดตั้งและรันระบบ (Quick Start)

### 1. เริ่มต้นระบบเซิร์ฟเวอร์ (Docker & Cloudflare Tunnel)
1. เปิดโปรแกรม **Docker Desktop** บนคอมพิวเตอร์
2. ดับเบิลคลิกไฟล์ `start_fastapi_docker.bat` เพื่อสั่งรัน Container:
   - FastAPI Dashboard จะเปิดที่ `http://localhost:8080` (หรือ Local IP `http://10.4.13.128:8080`)
   - ระบบจะตรวจจับ URL ของ Cloudflare Tunnel และอัปเดตปุ่มเมนู Telegram ให้อัตโนมัติ

### 2. อัปโหลดโค้ดลงบอร์ด Cucumber RS
1. เชื่อมต่อบอร์ด Cucumber RS เข้ากับคอมพิวเตอร์ผ่านสาย USB
2. เปิดไฟล์ `src/main.cpp` ตรวจสอบ SSID/Password Wi-Fi และ Endpoint
3. ดับเบิลคลิกไฟล์ `upload_board.bat` (หากบอร์ดค้าง ให้กดปุ่ม BOOT ค้างไว้ แล้วกดปุ่ม RESET 1 ครั้งเพื่อเข้า Download Mode)
4. ดับเบิลคลิก `serial_monitor.bat` เพื่อดูค่าเซนเซอร์และการส่งข้อมูล HTTP 200 OK

### 3. รันชุดทดสอบระบบครบวงจร (Test Suite)
- ดับเบิลคลิกไฟล์ `run_test.bat` เพื่อรันการจำลอง 6 ขั้นตอน:
  1. Health & Config Check
  2. Routine Telemetry Injection
  3. Edge-AI Fall Event Simulation (แรงกระแทก 3.85G)
  4. Caregiver Incident Resolve
  5. Gemini AI Chat Test
  6. Tunnel URL Sync Test

---

## 💬 การใช้งานผ่าน Telegram Bot (@bercuncu_bot)

ผู้ใช้สามารถสนทนากับ AI "น้องแตงกวา" ได้ทั้งภาษาธรรมชาติและคำสั่งลัด:
- `ตอนนี้อุณหภูมิเท่าไหร่` : ถามสภาพอากาศและอุณหภูมิห้องจริงจากเซนเซอร์ HTS221
- `วันนี้ล้มทั้งหมดกี่ครั้ง รุนแรงไหม` : สรุปประวัติการล้ม เวลา และค่าแรงกระแทก G
- `การใช้ชีวิตและสุขภาพวันนี้เป็นอย่างไร` : สรุปคะแนนสุขภาวะ (Activity Score) และสภาพห้องเฉลี่ย
- `/status` : ดูสถานะเซนเซอร์สดบนบอร์ด
- `/falls` : ดูรายงานประวัติการล้มของวันนี้และเมื่อวาน
- `/summary` : ดูสรุปสุขภาพและการใช้ชีวิตประจำวัน

---

## ⚠️ ข้อจำกัดและแนวทางการพัฒนาต่อยอด (Limitations & Future Work)

1. **อัลกอริทึมการตรวจจับการล้ม:** ปัจจุบันใช้ State Machine แบบ Threshold ซึ่งอาจเกิด False Positive ได้ง่ายหากกระโดดหรือทิ้งตัวลงฟูก แนวทางต่อยอดคือการพัฒนาโมเดล **TinyML (1D-CNN / Random Forest)** ที่ผ่านการเทรนบนชุดข้อมูลจริง
2. **การจัดการพลังงาน (Power Consumption):** บอร์ดรับส่ง Wi-Fi และประมวลผลตลอดเวลา ใช้กระแสไฟ 120-180mA การพัฒนาเป็นอุปกรณ์สวมใส่จริงจำเป็นต้องใช้โหมด Deep Sleep และ Motion Wakeup Interrupt
3. **การออกแบบทางกายภาพ:** ปัจจุบันเป็นแผงวงจรเปลือย ยังไม่มีกล่องกันกระแทกและกันน้ำ (IP67) ซึ่งจำเป็นสำหรับสถานการณ์ลื่นล้มในห้องน้ำจริง

---

## 🌐 เอกสารเผยแพร่บน GitHub Pages

หน้าเว็บเอกสารการเรียนรู้และแดชบอร์ดจำลองถูกจัดทำไว้ที่โฟลเดอร์ `/docs`:
- **หน้าหลักโครงการ (Overview & Interactive Demo):** [https://kkkking1919.github.io/mini_project_6610610161/](https://kkkking1919.github.io/mini_project_6610610161/)
- **หน้าแดชบอร์ดสด (Live Caregiver Dashboard):** [https://kkkking1919.github.io/mini_project_6610610161/dashboard.html](https://kkkking1919.github.io/mini_project_6610610161/dashboard.html)
- การเปิดใช้งาน: ไปที่ GitHub Repository > **Settings** > **Pages** > Branch: **main** > Folder: **/docs** > **Save**
