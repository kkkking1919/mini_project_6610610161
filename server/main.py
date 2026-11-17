import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

from database import init_db, get_db, SessionLocal
from models import FallEvent, Telemetry, DailySummary
from telegram_bot import TelegramBotService

# Initialize SQLite tables
init_db()

app = FastAPI(title="Edge-AI Fall Detection & Context-Aware Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware allowing Telegram Web to embed dashboard in iframe
@app.middleware("http")
async def allow_telegram_iframe(request, call_next):
    response = await call_next(request)
    try:
        response.headers["Content-Security-Policy"] = "frame-ancestors * 'self' https://*.telegram.org https://web.telegram.org;"
        if "x-frame-options" in response.headers:
            del response.headers["x-frame-options"]
    except Exception:
        pass
    return response

# Telegram Bot Config (Loaded via Environment / .env)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY_ENV = os.getenv("GEMINI_API_KEY", "")
cached_chat_id = TELEGRAM_CHAT_ID

def on_chat_id_discovered(new_id: str):
    global cached_chat_id
    if new_id and new_id != cached_chat_id:
        cached_chat_id = new_id
        print(f"[Main] Telegram chat_id registered from user chat: {new_id}")

async def get_or_detect_chat_id(token: str) -> Optional[str]:
    global cached_chat_id
    if cached_chat_id:
        return cached_chat_id
    if not token:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        async with httpx.AsyncClient(timeout=6.0) as client:
            res = await client.get(url)
            data = res.json()
            if data.get("ok") and data.get("result"):
                for update in reversed(data["result"]):
                    if "message" in update and "chat" in update["message"]:
                        detected = str(update["message"]["chat"]["id"])
                        cached_chat_id = detected
                        print(f"Auto-detected Telegram Chat ID: {detected}")
                        return detected
                    elif "channel_post" in update and "chat" in update["channel_post"]:
                        detected = str(update["channel_post"]["chat"]["id"])
                        cached_chat_id = detected
                        return detected
    except Exception as e:
        print(f"Telegram getUpdates detection error: {e}")
    return None

# In-memory fast cache
latest_data = {
    "temperature": 0.0,
    "humidity": 0.0,
    "pressure": 0.0,
    "rssi": -100,
    "accel": {"x": 0.0, "y": 0.0, "z": 0.0},
    "gyro": {"x": 0.0, "y": 0.0, "z": 0.0},
    "last_updated": None,
    "is_connected": False
}

active_emergency = {
    "active": False,
    "id": None,
    "timestamp": None,
    "max_g": 0.0,
    "temperature": 0.0,
    "humidity": 0.0,
    "pressure": 0.0,
    "risk": None
}

TUNNEL_PUBLIC_URL = os.getenv("TUNNEL_URL", "https://broken-enter-recommendations-classroom.trycloudflare.com")

def get_fall_history_for_bot() -> dict:
    """Query today's and recent fall incidents from SQLite for Telegram Bot."""
    db = SessionLocal()
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Today's falls
        today_falls = db.query(FallEvent).filter(FallEvent.timestamp.like(f"{today_str}%")).order_by(FallEvent.id.asc()).all()
        # Yesterday's falls
        yesterday_falls = db.query(FallEvent).filter(FallEvent.timestamp.like(f"{yesterday_str}%")).all()
        # Recent 7 days falls
        all_recent_falls = db.query(FallEvent).order_by(FallEvent.id.desc()).limit(20).all()

        today_list = []
        for f in today_falls:
            time_part = f.timestamp.split()[1] if " " in f.timestamp else f.timestamp
            today_list.append({
                "time": time_part,
                "max_g": f.max_g,
                "risk": f.context_risk,
                "status": f.status,
                "temp": f.temperature,
                "humidity": f.humidity
            })

        recent_days = {}
        for f in all_recent_falls:
            day = f.timestamp.split()[0] if " " in f.timestamp else f.timestamp
            recent_days[day] = recent_days.get(day, 0) + 1

        return {
            "today_count": len(today_falls),
            "today_incidents": today_list,
            "yesterday_count": len(yesterday_falls),
            "recent_days_count": recent_days
        }
    except Exception as e:
        print(f"[Main] Error querying fall history: {e}")
        return {"today_count": 0, "today_incidents": [], "yesterday_count": 0, "recent_days_count": {}}
    finally:
        db.close()

def get_lifestyle_summary_for_bot() -> dict:
    """Query today's telemetry statistics and activity score for Telegram Bot."""
    db = SessionLocal()
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        telems = db.query(Telemetry).filter(Telemetry.timestamp.like(f"{today_str}%")).all()
        falls_count = db.query(FallEvent).filter(FallEvent.timestamp.like(f"{today_str}%")).count()
        latest_summary = db.query(DailySummary).order_by(DailySummary.id.desc()).first()

        total_samples = len(telems)
        if total_samples > 0:
            avg_temp = round(sum(t.temperature for t in telems if t.temperature) / total_samples, 1)
            avg_hum = round(sum(t.humidity for t in telems if t.humidity) / total_samples, 1)
            avg_press = round(sum(t.pressure for t in telems if t.pressure) / total_samples, 1)
        else:
            avg_temp = latest_data["temperature"] or "--"
            avg_hum = latest_data["humidity"] or "--"
            avg_press = latest_data["pressure"] or "--"

        score = max(100 - (falls_count * 25), 25)
        if latest_summary and latest_summary.date == today_str:
            score = latest_summary.activity_score

        return {
            "date": today_str,
            "score": score,
            "total_telemetry_samples": total_samples,
            "avg_temp": avg_temp,
            "avg_hum": avg_hum,
            "avg_press": avg_press,
            "falls_count": falls_count,
            "latest_ai_summary": latest_summary.summary_text if latest_summary else None
        }
    except Exception as e:
        print(f"[Main] Error querying lifestyle summary: {e}")
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "score": 85,
            "total_telemetry_samples": 0,
            "avg_temp": "--",
            "avg_hum": "--",
            "avg_press": "--",
            "falls_count": 0,
            "latest_ai_summary": None
        }
    finally:
        db.close()

# Initialize Telegram Conversational AI Service
tg_bot_service = TelegramBotService(
    token=TELEGRAM_BOT_TOKEN,
    gemini_api_key=GEMINI_API_KEY_ENV,
    get_sensor_data_fn=lambda: latest_data,
    get_emergency_status_fn=lambda: active_emergency,
    get_fall_history_fn=get_fall_history_for_bot,
    get_lifestyle_summary_fn=get_lifestyle_summary_for_bot,
    app_url=TUNNEL_PUBLIC_URL,
    on_chat_id_discovered=on_chat_id_discovered
)

@app.on_event("startup")
async def startup_event():
    tg_bot_service.start()
    if TUNNEL_PUBLIC_URL:
        await tg_bot_service.setup_menu_button(TUNNEL_PUBLIC_URL)

@app.on_event("shutdown")
async def shutdown_event():
    tg_bot_service.stop()

class ChatMessageRequest(BaseModel):
    chat_id: str = "web_user"
    message: str

@app.post("/api/chat")
async def chat_with_ai(body: ChatMessageRequest):
    cid = body.chat_id or "web_user"
    reply = await tg_bot_service.ask_gemini(cid, body.message)
    return {"status": "success", "reply": reply}

@app.get("/api/chat/history")
async def get_chat_history_api(chat_id: str = "web_user"):
    history = tg_bot_service.get_chat_history(chat_id)
    return {"status": "success", "history": history}

history_data = []

class AccelData(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

class GyroData(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

class SensorPayload(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    rssi: Optional[int] = None
    accel: Optional[AccelData] = None
    gyro: Optional[GyroData] = None

class FallEventPayload(BaseModel):
    max_g: float
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None

class AISummaryRequest(BaseModel):
    api_key: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

class TelegramTestPayload(BaseModel):
    token: Optional[str] = None
    chat_id: Optional[str] = None
    message: Optional[str] = None

async def send_telegram_alert(token: str, chat_id: str, message: str) -> dict:
    bot_token = token or TELEGRAM_BOT_TOKEN
    target_chat_id = chat_id or cached_chat_id or await get_or_detect_chat_id(bot_token)
    if not bot_token:
        return {"status": "error", "message": "ไม่ได้ตั้งค่า Telegram Bot Token"}
    if not target_chat_id:
        print("Telegram alert skipped: No chat_id detected yet. Please message @bercuncu_bot.")
        return {
            "status": "waiting_chat",
            "message": "ยังไม่พบ Chat ID กรุณาเปิด Telegram ค้นหา @bercuncu_bot แล้วกด Start หรือส่งข้อความทักทายก่อนครับ"
        }
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.post(url, json=payload)
            res_data = res.json()
            if not res_data.get("ok"):
                return {"status": "error", "detail": res_data.get("description", "Unknown error")}
            return {"status": "success", "chat_id": target_chat_id}
    except Exception as e:
        print(f"Telegram dispatch error: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/api/config")
async def get_config():
    global cached_chat_id
    if not cached_chat_id and TELEGRAM_BOT_TOKEN:
        await get_or_detect_chat_id(TELEGRAM_BOT_TOKEN)
    return {
        "gemini_ready": bool(GEMINI_API_KEY_ENV),
        "telegram_ready": bool(TELEGRAM_BOT_TOKEN),
        "telegram_chat_id": cached_chat_id,
        "bot_username": "bercuncu_bot"
    }

@app.post("/api/telegram/test")
async def test_telegram_alert(body: TelegramTestPayload = TelegramTestPayload()):
    tok = body.token or TELEGRAM_BOT_TOKEN
    cid = body.chat_id or TELEGRAM_CHAT_ID
    msg = body.message or (
        "🔔 *ทดสอบการแจ้งเตือนจาก Cucumber RS Edge-AI!*\n\n"
        "✅ บอทเชื่อมต่อกับระบบสำเร็จเรียบร้อยแล้ว\n"
        "พร้อมรับการแจ้งเตือนอุบัติเหตุการล้มแบบเรียลไทม์ และสรุปรายงานสุขภาวะด้วย AI ครับ 🚑"
    )
    result = await send_telegram_alert(tok, cid, msg)
    return result

class TunnelUrlPayload(BaseModel):
    url: str

@app.post("/api/config/tunnel-url")
async def update_tunnel_url(body: TunnelUrlPayload):
    global TUNNEL_PUBLIC_URL
    new_url = body.url.strip().rstrip("/")
    if not (new_url.startswith("http://") or new_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid URL: must start with http:// or https://")

    TUNNEL_PUBLIC_URL = new_url
    print(f"\n[Tunnel Sync] 🌐 New Cloudflare Tunnel URL received: {new_url}")

    # 1. Update Telegram Persistent Menu Button & in-memory app_url
    await tg_bot_service.setup_menu_button(new_url)

    # 2. Persist to server/.env so it survives restarts
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("TUNNEL_URL="):
                    new_lines.append(f"TUNNEL_URL={new_url}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"TUNNEL_URL={new_url}\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            print(f"[Tunnel Sync] 💾 Saved new TUNNEL_URL to {env_path}")
    except Exception as e:
        print(f"[Tunnel Sync] Error persisting to .env: {e}")

    # 3. Broadcast notification to Telegram
    target_chat = cached_chat_id or TELEGRAM_CHAT_ID
    if target_chat and TELEGRAM_BOT_TOKEN:
        notify_msg = (
            f"🚀 *เซิร์ฟเวอร์ออนไลน์พร้อมใช้งาน (Server Online!)*\n\n"
            f"🔗 *URL เว็บ Dashboard & Telegram Mini App:*\n"
            f"`{new_url}`\n\n"
            f"📱 แตะปุ่ม *'📱 เปิดแอปสุขภาพ'* ที่แถบเมนูด้านล่าง หรือกดปุ่มด้านล่างเพื่อเปิดใช้งานได้ทันทีครับ"
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀 เปิด Cucumber RS Mini App",
                        "web_app": {"url": new_url}
                    }
                ]
            ]
        }
        asyncio.create_task(tg_bot_service.send_reply(target_chat, notify_msg, reply_markup=reply_markup))

    return {
        "status": "success",
        "tunnel_url": new_url,
        "message": "Updated tunnel URL, Telegram Menu Button, and notified chat successfully"
    }

@app.get("/api/config/tunnel-url")
async def get_current_tunnel_url():
    return {
        "status": "success",
        "tunnel_url": tg_bot_service.app_url or TUNNEL_PUBLIC_URL
    }

@app.post("/api/sensor-data")
async def receive_sensor_data(payload: SensorPayload, db: Session = Depends(get_db)):
    global latest_data, history_data
    if payload.temperature is not None:
        latest_data["temperature"] = round(payload.temperature, 2)
    if payload.humidity is not None:
        latest_data["humidity"] = round(payload.humidity, 2)
    if payload.pressure is not None:
        latest_data["pressure"] = round(payload.pressure, 2)
    if payload.rssi is not None:
        latest_data["rssi"] = payload.rssi
    if payload.accel is not None:
        latest_data["accel"] = {
            "x": round(payload.accel.x, 2),
            "y": round(payload.accel.y, 2),
            "z": round(payload.accel.z, 2),
        }
    if payload.gyro is not None:
        latest_data["gyro"] = {
            "x": round(payload.gyro.x, 3),
            "y": round(payload.gyro.y, 3),
            "z": round(payload.gyro.z, 3),
        }
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latest_data["last_updated"] = timestamp
    latest_data["is_connected"] = True
    print(f"[Telemetry] 📥 Real-time Board Data Received: Temp={latest_data['temperature']}°C, Hum={latest_data['humidity']}%RH, Pres={latest_data['pressure']}hPa")

    # In-memory history
    record = {
        "id": len(history_data) + 1,
        "timestamp": timestamp,
        "temperature": latest_data["temperature"],
        "humidity": latest_data["humidity"],
        "pressure": latest_data["pressure"],
        "rssi": latest_data["rssi"]
    }
    history_data.insert(0, record)
    if len(history_data) > 100:
        history_data.pop()

    # Save to SQLite Telemetry
    try:
        telem = Telemetry(
            timestamp=timestamp,
            temperature=latest_data["temperature"],
            humidity=latest_data["humidity"],
            pressure=latest_data["pressure"],
            rssi=latest_data["rssi"],
            accel_x=latest_data["accel"]["x"],
            accel_y=latest_data["accel"]["y"],
            accel_z=latest_data["accel"]["z"],
            gyro_x=latest_data["gyro"]["x"],
            gyro_y=latest_data["gyro"]["y"],
            gyro_z=latest_data["gyro"]["z"],
        )
        db.add(telem)
        db.commit()
    except Exception as e:
        db.rollback()
        print("DB Telemetry insert error:", e)

    return {"status": "success", "message": "Telemetry received"}

@app.post("/api/fall-event")
async def receive_fall_event(payload: FallEventPayload, db: Session = Depends(get_db)):
    global active_emergency
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    max_g = round(payload.max_g, 2)
    temp = round(payload.temperature, 2) if payload.temperature is not None else latest_data["temperature"]
    hum = round(payload.humidity, 2) if payload.humidity is not None else latest_data["humidity"]
    press = round(payload.pressure, 2) if payload.pressure is not None else latest_data["pressure"]

    # Context-aware risk evaluation
    risk_level = "เสี่ยงปานกลาง: ตรวจพบแรงกระแทกจากการล้ม"
    if hum >= 75:
        risk_level = "🚨 เสี่ยงสูงมาก: ความชื้นสูงผิดปกติ (อาจลื่นล้มในห้องน้ำ/พื้นเปียก)"
    elif temp >= 38.0:
        risk_level = "🚨 เสี่ยงสูง: อุณหภูมิสูงผิดปกติ (อาจมีภาวะ Heat Stroke/หมดสติ)"
    elif max_g >= 3.5:
        risk_level = "⚠️ เสี่ยงสูง: แรงกระแทกสูงรุนแรง (> 3.5G) เฝ้าระวังศีรษะ/กระดูกหัก"

    # Save to SQLite
    event = FallEvent(
        timestamp=timestamp,
        max_g=max_g,
        temperature=temp,
        humidity=hum,
        pressure=press,
        context_risk=risk_level,
        status="PENDING"
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Set active emergency state
    active_emergency = {
        "active": True,
        "id": event.id,
        "timestamp": timestamp,
        "max_g": max_g,
        "temperature": temp,
        "humidity": hum,
        "pressure": press,
        "risk": risk_level
    }

    # Dispatch Telegram Alert
    msg = (
        f"🚨 *แจ้งเตือนเหตุฉุกเฉิน: ตรวจพบการล้ม (Fall Detected!)*\n\n"
        f"📅 *เวลา:* `{timestamp}`\n"
        f"⚡ *แรงกระแทกสูงสุด:* `{max_g} G`\n"
        f"🌡️ *อุณหภูมิบริบท:* `{temp} °C`\n"
        f"💧 *ความชื้นบริบท:* `{hum} %RH`\n"
        f"🌪️ *ความกดอากาศ:* `{press} hPa`\n"
        f"🔍 *ประเมินบริบท:* {risk_level}\n\n"
        f"👉 *กรุณาตรวจสอบผู้ใช้งานทันที!*"
    )
    await send_telegram_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)

    return {"status": "success", "event_id": event.id, "risk": risk_level}

@app.get("/api/sensor-data")
async def get_sensor_data():
    return latest_data

@app.get("/api/emergency-status")
async def get_emergency_status():
    return active_emergency

@app.post("/api/fall-event/{event_id}/resolve")
async def resolve_fall_event(event_id: int, db: Session = Depends(get_db)):
    global active_emergency
    event = db.query(FallEvent).filter(FallEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event.status = "RESOLVED"
    db.commit()

    if active_emergency["id"] == event_id:
        active_emergency["active"] = False

    return {"status": "success", "message": "Event marked as resolved"}

@app.get("/api/fall-events")
async def get_fall_events(db: Session = Depends(get_db)):
    events = db.query(FallEvent).order_by(FallEvent.id.desc()).limit(50).all()
    return {"events": [
        {
            "id": e.id,
            "timestamp": e.timestamp,
            "max_g": e.max_g,
            "temperature": e.temperature,
            "humidity": e.humidity,
            "pressure": e.pressure,
            "context_risk": e.context_risk,
            "status": e.status
        } for e in events
    ]}

@app.get("/api/history")
async def get_history():
    return {"history": history_data, "total": len(history_data)}

@app.post("/api/history/clear")
async def clear_history():
    global history_data
    history_data.clear()
    return {"status": "success", "message": "History cleared"}

@app.post("/api/ai/daily-summary")
async def generate_ai_daily_summary(body: AISummaryRequest, db: Session = Depends(get_db)):
    api_key = body.api_key or GEMINI_API_KEY_ENV
    if not api_key:
        raise HTTPException(status_code=400, detail="กรุณาระบุ Google Gemini API Key เพื่อใช้งานฟังก์ชันนี้ครับ")

    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Query today's telemetry & fall events from SQLite
    telems = db.query(Telemetry).filter(Telemetry.timestamp.like(f"{today_str}%")).all()
    falls = db.query(FallEvent).filter(FallEvent.timestamp.like(f"{today_str}%")).all()

    total_samples = len(telems)
    avg_temp = round(sum(t.temperature for t in telems if t.temperature) / max(total_samples, 1), 2)
    avg_hum = round(sum(t.humidity for t in telems if t.humidity) / max(total_samples, 1), 2)
    avg_press = round(sum(t.pressure for t in telems if t.pressure) / max(total_samples, 1), 2)
    falls_count = len(falls)

    fall_details = ""
    for f in falls:
        fall_details += f"- เวลา {f.timestamp}: แรงกระแทก {f.max_g}G, ความชื้น {f.humidity}%, อุณหภูมิ {f.temperature}°C, ความกดอากาศ {f.pressure} hPa, การประเมิน: {f.context_risk}\n"
    if not fall_details:
        fall_details = "ไม่มีบันทึกเหตุการณ์การล้มในวันนี้ (ปลอดภัย)"

    if falls_count > 0:
        incident_data = f"บันทึกเหตุการณ์การล้มที่เกิดขึ้นในวันนี้ ({today_str}) ทั้งหมด {falls_count} ครั้ง:\n{fall_details}"
    else:
        incident_data = f"ไม่มีบันทึกเหตุการณ์การล้มในวันนี้ ({today_str}) (สถานะ: ปลอดภัย)"

    # Formulate Concise Fall Incident & Context Gemini Prompt
    prompt = f"""
คุณเป็นระบบ AI รายงานเหตุการณ์การล้มและบริบทสิ่งแวดล้อม สำหรับอุปกรณ์สวมใส่ Cucumber RS
หน้าที่: รายงานสรุปแบบ "สั้น กระชับ ตรงประเด็น (ไม่เวิ่นเว้อ)" โดยรายงานเฉพาะ "เหตุการณ์การล้มและบริบทสิ่งแวดล้อมขณะเกิดเหตุ" เท่านั้น
ข้อห้าม: ไม่ต้องเกริ่นนำหรือลงท้ายยาว, ไม่ต้องวิเคราะห์กิจวัตรทั่วไป, และไม่ต้องให้คำแนะนำทางการแพทย์ที่ยาวเกินไป

ข้อมูลเหตุการณ์:
{incident_data}

ข้อกำหนดรูปแบบการตอบ (ความยาวสั้นกระชับ):
- กรณีตรวจพบการล้ม:
  รายงานเป็น Bullet points สั้นๆ ชัดเจนสำหรับเหตุการณ์ที่เกิดขึ้น:
  • 🚨 **เหตุการณ์ล้ม:** ระบุเวลา, แรงกระแทก (G) และระดับความรุนแรง
  • 🌡️ **บริบทสิ่งแวดล้อมขณะเกิดเหตุ:** อุณหภูมิ, ความชื้น, และปัจจัยเสี่ยงจากสิ่งแวดล้อมขณะนั้น (เช่น ความชื้นสูงเสี่ยงพื้นลื่นในห้องน้ำ หรืออากาศร้อนเสี่ยงวูบ/เป็นลม)
  • ⚠️ **ข้อควรระวังเร่งด่วน:** คำแนะนำสั้นๆ 1 ประโยคสำหรับผู้ดูแล
- กรณีไม่พบการล้ม:
  รายงานสั้นๆ 1-2 บรรทัดว่า: วันนี้ไม่พบเหตุการณ์การล้ม ผู้สวมใส่อยู่ในสภาวะปลอดภัย บริบทสภาพแวดล้อมอยู่ในเกณฑ์ปกติ
"""

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        ai_text = response.text
    except Exception as e:
        # Fallback to direct HTTP request if SDK encounters version mismatch
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            req_body = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url, json=req_body)
                res_json = res.json()
                ai_text = res_json['candidates'][0]['content']['parts'][0]['text']
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาดในการเรียก Gemini API: {str(e2)}")

    # Save to SQLite
    score = max(100 - (falls_count * 30), 20)
    summary_record = DailySummary(
        date=today_str,
        summary_text=ai_text,
        activity_score=score,
        falls_count=falls_count
    )
    db.add(summary_record)
    db.commit()

    # Also dispatch summary to Telegram if available
    try:
        tg_summary_msg = (
            f"🚨 *รายงานสรุปเหตุการณ์การล้มและบริบทแวดล้อม (Fall Incident & Context)*\n"
            f"📅 *วันที่:* `{today_str}` | 🚨 *พบการล้ม:* `{falls_count} ครั้ง`\n\n"
            f"{ai_text[:3500]}"
        )
        await send_telegram_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, tg_summary_msg)
    except Exception as e:
        print("Telegram summary dispatch error:", e)

    return {
        "status": "success",
        "date": today_str,
        "score": score,
        "falls_count": falls_count,
        "summary": ai_text
    }

@app.get("/api/ai/daily-summary/latest")
async def get_latest_daily_summary(db: Session = Depends(get_db)):
    summary = db.query(DailySummary).order_by(DailySummary.id.desc()).first()
    if not summary:
        return {"summary": None}
    return {
        "id": summary.id,
        "date": summary.date,
        "summary": summary.summary_text,
        "score": summary.activity_score,
        "falls_count": summary.falls_count,
        "created_at": summary.created_at
    }

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    html_content = """
<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Cucumber RS - Telegram Mini App & Dashboard</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap');
    body { font-family: 'Prompt', sans-serif; }
    .glass {
      background: rgba(30, 41, 59, 0.75);
      backdrop-filter: blur(14px);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .emergency-glow {
      box-shadow: 0 0 50px rgba(239, 68, 68, 0.6);
      animation: emergencyPulse 1.2s infinite alternate;
    }
    @keyframes emergencyPulse {
      0% { box-shadow: 0 0 25px rgba(239, 68, 68, 0.4); border-color: rgba(239, 68, 68, 0.6); }
      100% { box-shadow: 0 0 60px rgba(239, 68, 68, 0.9); border-color: rgba(239, 68, 68, 1); }
    }
    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.6); }
    ::-webkit-scrollbar-thumb { background: rgba(100, 116, 139, 0.5); border-radius: 9999px; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen relative pb-24">

  <!-- EMERGENCY ALERT MODAL -->
  <div id="emergency-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
    <div class="glass emergency-glow max-w-lg w-full rounded-3xl p-7 border-2 border-rose-500 bg-slate-900/95 text-center space-y-5">
      <div class="w-20 h-20 mx-auto rounded-full bg-rose-500/20 text-rose-500 flex items-center justify-center text-4xl animate-bounce">
        <i class="fa-solid fa-person-falling-burst"></i>
      </div>
      <div>
        <span class="px-3 py-1 rounded-full text-xs font-bold uppercase tracking-widest bg-rose-500/20 text-rose-400 border border-rose-500/30">
          EMERGENCY ALERT
        </span>
        <h2 class="text-2xl font-black text-white mt-2">ตรวจพบการล้ม! (Fall Detected)</h2>
        <p id="emergency-time" class="text-xs text-rose-300 font-mono mt-1">-</p>
      </div>

      <!-- Incident Context Details -->
      <div class="bg-slate-950/80 rounded-2xl p-4 border border-rose-500/30 text-left space-y-2.5 text-xs">
        <div class="flex justify-between items-center pb-2 border-b border-slate-800">
          <span class="text-slate-400">แรงกระแทกสูงสุด (Max Impact):</span>
          <span id="emergency-g" class="font-bold text-rose-400 text-sm font-mono">-- G</span>
        </div>
        <div class="grid grid-cols-2 gap-2 pb-2 border-b border-slate-800">
          <div>
            <span class="text-slate-400">อุณหภูมิบริบท:</span>
            <div id="emergency-temp" class="font-bold text-amber-400 font-mono">-- °C</div>
          </div>
          <div>
            <span class="text-slate-400">ความชื้นสัมพัทธ์:</span>
            <div id="emergency-hum" class="font-bold text-sky-400 font-mono">-- %RH</div>
          </div>
        </div>
        <div>
          <span class="text-slate-400 block mb-1">การประเมินบริบท (Context Risk):</span>
          <div id="emergency-risk" class="p-2.5 rounded-xl bg-rose-950/40 text-rose-200 border border-rose-900/60 font-medium">
            --
          </div>
        </div>
      </div>

      <div class="flex gap-3 pt-2">
        <button onclick="resolveEmergency()" class="flex-1 py-3 px-4 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-sm shadow-lg shadow-rose-600/30 transition-all flex items-center justify-center gap-2">
          <i class="fa-solid fa-check"></i> รับทราบเหตุการณ์ (Acknowledge)
        </button>
      </div>
    </div>
  </div>

  <!-- Top Navigation Bar -->
  <header class="glass sticky top-0 z-40 px-6 py-4 border-b border-slate-800">
    <div class="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between items-center gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-xl font-bold shadow-lg shadow-emerald-500/10">
          🥒
        </div>
        <div>
          <h1 class="text-xl font-bold text-white tracking-wide">Edge-AI Fall Detection & Context Node</h1>
          <p class="text-xs text-slate-400">Cucumber RS (ESP32-S2) Wearable • Mini Project CN466</p>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <span id="tma-badge" class="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-500/20 text-xs font-medium">
          <i class="fa-brands fa-telegram text-sky-400"></i> Telegram Mini App
        </span>
        <span class="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 text-xs font-medium">
          <i class="fa-solid fa-brain text-indigo-400"></i> Gemini 2.5 Flash
        </span>
        <a href="https://t.me/bercuncu_bot" target="_blank" title="คลิกเพื่อเปิดคุยกับ Telegram Bot" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-sky-500/10 text-sky-300 hover:bg-sky-500/20 border border-sky-500/20 text-xs font-medium transition-all">
          <i class="fa-brands fa-telegram text-sky-400"></i> @bercuncu_bot
        </a>
        <button onclick="openConfigModal()" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium flex items-center gap-1.5 border border-slate-700/60 transition-all">
          <i class="fa-solid fa-gear"></i> ตั้งค่า API / บอท
        </button>
        <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/80 border border-slate-700/60 text-xs">
          <span id="status-dot" class="w-2.5 h-2.5 rounded-full bg-rose-500 animate-pulse"></span>
          <span id="status-text" class="text-slate-300 font-medium">รอการเชื่อมต่อ...</span>
        </div>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 py-6">

    <!-- TAB 1: DASHBOARD VIEW -->
    <div id="view-dashboard" class="space-y-8">

    <!-- 4 Main Primary Sensor Cards -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
      <!-- Temperature (HTS221) -->
      <div class="glass rounded-2xl p-6 relative overflow-hidden transition-all hover:scale-[1.02] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <span class="text-xs font-semibold uppercase tracking-wider text-amber-400/90">HTS221 Sensor</span>
          <div class="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center text-amber-400">
            <i class="fa-solid fa-temperature-half text-xl"></i>
          </div>
        </div>
        <div class="text-xs text-slate-400 mb-1">อุณหภูมิ (Temperature)</div>
        <div class="flex items-baseline gap-2">
          <span id="temp-val" class="text-4xl font-extrabold text-white tracking-tight">--</span>
          <span class="text-lg font-medium text-slate-400">°C</span>
        </div>
        <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
          <span>ความรู้สึกจริง</span>
          <span id="temp-status" class="text-amber-400 font-medium">ปกติ</span>
        </div>
      </div>

      <!-- Humidity (HTS221) -->
      <div class="glass rounded-2xl p-6 relative overflow-hidden transition-all hover:scale-[1.02] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <span class="text-xs font-semibold uppercase tracking-wider text-sky-400/90">HTS221 Sensor</span>
          <div class="w-10 h-10 rounded-xl bg-sky-500/10 flex items-center justify-center text-sky-400">
            <i class="fa-solid fa-droplet text-xl"></i>
          </div>
        </div>
        <div class="text-xs text-slate-400 mb-1">ความชื้นสัมพัทธ์ (Humidity)</div>
        <div class="flex items-baseline gap-2">
          <span id="hum-val" class="text-4xl font-extrabold text-white tracking-tight">--</span>
          <span class="text-lg font-medium text-slate-400">%RH</span>
        </div>
        <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
          <span>ระดับความชื้น</span>
          <span id="hum-status" class="text-sky-400 font-medium">--</span>
        </div>
      </div>

      <!-- Barometric Pressure (BMP280) -->
      <div class="glass rounded-2xl p-6 relative overflow-hidden transition-all hover:scale-[1.02] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <span class="text-xs font-semibold uppercase tracking-wider text-indigo-400/90">BMP280 Sensor</span>
          <div class="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center text-indigo-400">
            <i class="fa-solid fa-gauge-high text-xl"></i>
          </div>
        </div>
        <div class="text-xs text-slate-400 mb-1">ความกดอากาศ (Pressure)</div>
        <div class="flex items-baseline gap-2">
          <span id="press-val" class="text-4xl font-extrabold text-white tracking-tight">--</span>
          <span class="text-lg font-medium text-slate-400">hPa</span>
        </div>
        <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
          <span>ความกดอากาศมาตรฐาน</span>
          <span class="text-indigo-300 font-medium">~1013.25 hPa</span>
        </div>
      </div>

      <!-- WiFi RSSI -->
      <div class="glass rounded-2xl p-6 relative overflow-hidden transition-all hover:scale-[1.02] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <span class="text-xs font-semibold uppercase tracking-wider text-emerald-400/90">WiFi Signal</span>
          <div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400">
            <i class="fa-solid fa-wifi text-xl"></i>
          </div>
        </div>
        <div class="text-xs text-slate-400 mb-1">ความแรงสัญญาณ (RSSI)</div>
        <div class="flex items-baseline gap-2">
          <span id="rssi-val" class="text-4xl font-extrabold text-white tracking-tight">--</span>
          <span class="text-lg font-medium text-slate-400">dBm</span>
        </div>
        <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
          <span>คุณภาพสัญญาณ</span>
          <span id="rssi-status" class="text-emerald-400 font-medium">--</span>
        </div>
      </div>
    </div>

    <!-- AI FALL INCIDENT & CONTEXT REPORT SECTION -->
    <div class="glass rounded-3xl p-7 shadow-2xl border border-indigo-500/30 relative overflow-hidden">
      <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 pb-5 border-b border-slate-800">
        <div class="flex items-center gap-3.5">
          <div class="w-12 h-12 rounded-2xl bg-gradient-to-tr from-indigo-600 to-purple-600 text-white flex items-center justify-center text-2xl shadow-lg shadow-indigo-500/25">
            <i class="fa-solid fa-person-falling-burst"></i>
          </div>
          <div>
            <h2 class="text-xl font-bold text-white flex items-center gap-2">
              AI Incident & Context Summarizer
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 text-white uppercase">Google Gemini</span>
            </h2>
            <p class="text-xs text-slate-400">รายงานสรุปเฉพาะเหตุการณ์การล้มและบริบทสิ่งแวดล้อมขณะเกิดเหตุ (สั้น กระชับ ตรงประเด็น)</p>
          </div>
        </div>
        <button onclick="generateAISummary()" id="ai-generate-btn" class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-xs font-semibold flex items-center gap-2 shadow-lg shadow-indigo-600/25 transition-all">
          <i class="fa-solid fa-wand-magic-sparkles"></i> สรุปเหตุการณ์การล้มด้วย AI
        </button>
      </div>

      <!-- AI Content Box -->
      <div id="ai-summary-box" class="mt-5 p-5 rounded-2xl bg-slate-900/80 border border-slate-800 text-slate-300 text-sm leading-relaxed min-h-[100px]">
        <div id="ai-placeholder" class="text-center py-5 text-slate-500 text-xs font-sans">
          <i class="fa-solid fa-sparkles text-xl mb-2 text-indigo-400 block"></i>
          กดปุ่ม <b>"สรุปเหตุการณ์การล้มด้วย AI"</b> เพื่อให้ Google Gemini สรุปเหตุการณ์และบริบทแวดล้อมขณะเกิดเหตุ
        </div>
        <div id="ai-content" class="hidden prose prose-invert max-w-none text-xs sm:text-sm"></div>
      </div>
    </div>

    <!-- MPU-6050 Motion Monitor Visual Bars -->
    <div class="glass rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-6 pb-3 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-purple-500/20 text-purple-400 flex items-center justify-center text-xl">
            <i class="fa-solid fa-compass"></i>
          </div>
          <div>
            <h2 class="text-lg font-bold text-white">MPU-6050 Motion Monitor (6-Axis IMU)</h2>
            <p class="text-xs text-slate-400">เซนเซอร์ตรวจจับความเร่งและการหมุน (Address: 0x68)</p>
          </div>
        </div>
        <span class="text-xs px-2.5 py-1 rounded bg-purple-500/10 text-purple-300 border border-purple-500/20 font-mono">I2C: 0x68</span>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Accelerometer Bars -->
        <div class="bg-slate-900/60 rounded-xl p-5 border border-slate-800/80">
          <div class="flex items-center justify-between mb-4">
            <span class="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <i class="fa-solid fa-arrows-up-down-left-right text-purple-400"></i> ความเร่ง (Acceleration)
            </span>
            <span class="text-xs text-slate-400 font-mono">m/s²</span>
          </div>
          <div class="space-y-4">
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Axis X:</span>
                <span id="accel-x" class="font-mono text-purple-300 font-bold">0.00</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="accel-x-bar" class="bg-purple-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Axis Y:</span>
                <span id="accel-y" class="font-mono text-purple-300 font-bold">0.00</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="accel-y-bar" class="bg-purple-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Axis Z (Gravity):</span>
                <span id="accel-z" class="font-mono text-purple-300 font-bold">9.81</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="accel-z-bar" class="bg-purple-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- Gyroscope Bars -->
        <div class="bg-slate-900/60 rounded-xl p-5 border border-slate-800/80">
          <div class="flex items-center justify-between mb-4">
            <span class="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <i class="fa-solid fa-rotate text-cyan-400"></i> ไจโรสโคป (Gyroscope)
            </span>
            <span class="text-xs text-slate-400 font-mono">rad/s</span>
          </div>
          <div class="space-y-4">
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Gyro X:</span>
                <span id="gyro-x" class="font-mono text-cyan-300 font-bold">0.000</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="gyro-x-bar" class="bg-cyan-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Gyro Y:</span>
                <span id="gyro-y" class="font-mono text-cyan-300 font-bold">0.000</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="gyro-y-bar" class="bg-cyan-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="text-slate-400 font-semibold">Gyro Z:</span>
                <span id="gyro-z" class="font-mono text-cyan-300 font-bold">0.000</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div id="gyro-z-bar" class="bg-cyan-500 h-2 rounded-full transition-all duration-300" style="width: 50%"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 3 REAL-TIME CHARTS (Stacked Vertically) -->
    
    <!-- 1. Environmental Chart (Temp & Humidity) -->
    <div class="glass rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center text-sm">
            <i class="fa-solid fa-cloud-sun"></i>
          </div>
          <div>
            <h2 class="text-base font-bold text-white">1. กราฟสภาพแวดล้อม (Environmental Trends)</h2>
            <p class="text-xs text-slate-400">อุณหภูมิ (°C) และความชื้นสัมพัทธ์ (%RH) แบบเรียลไทม์</p>
          </div>
        </div>
        <span class="text-xs text-slate-400">HTS221</span>
      </div>
      <div class="h-64">
        <canvas id="envChart"></canvas>
      </div>
    </div>

    <!-- 2. Acceleration Chart (X, Y, Z) -->
    <div class="glass rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-rose-500/20 text-rose-400 flex items-center justify-center text-sm">
            <i class="fa-solid fa-arrows-up-down-left-right"></i>
          </div>
          <div>
            <h2 class="text-base font-bold text-white">2. กราฟความเร่ง 3 แกน (Acceleration X, Y, Z)</h2>
            <p class="text-xs text-slate-400">วัดความเร่งตามแนวแกน X, Y, Z ในหน่วย m/s² จาก MPU-6050</p>
          </div>
        </div>
        <div class="flex items-center gap-3 text-xs font-mono">
          <span class="flex items-center gap-1.5 text-rose-400"><span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span>X</span>
          <span class="flex items-center gap-1.5 text-emerald-400"><span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>Y</span>
          <span class="flex items-center gap-1.5 text-blue-400"><span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span>Z</span>
        </div>
      </div>
      <div class="h-64">
        <canvas id="accelChart"></canvas>
      </div>
    </div>

    <!-- 3. Gyroscope Chart (X, Y, Z) -->
    <div class="glass rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-sm">
            <i class="fa-solid fa-rotate"></i>
          </div>
          <div>
            <h2 class="text-base font-bold text-white">3. กราฟไจโรสโคป 3 แกน (Gyroscope X, Y, Z)</h2>
            <p class="text-xs text-slate-400">วัดความเร็วเชิงมุมการหมุน X, Y, Z ในหน่วย rad/s จาก MPU-6050</p>
          </div>
        </div>
        <div class="flex items-center gap-3 text-xs font-mono">
          <span class="flex items-center gap-1.5 text-orange-400"><span class="w-2.5 h-2.5 rounded-full bg-orange-500"></span>X</span>
          <span class="flex items-center gap-1.5 text-purple-400"><span class="w-2.5 h-2.5 rounded-full bg-purple-500"></span>Y</span>
          <span class="flex items-center gap-1.5 text-cyan-400"><span class="w-2.5 h-2.5 rounded-full bg-cyan-500"></span>Z</span>
        </div>
      </div>
      <div class="h-64">
        <canvas id="gyroChart"></canvas>
      </div>
    </div>

    <!-- 4. Fall Incidents Log Table (SQLite Persistent) -->
    <div class="glass rounded-2xl p-6 shadow-xl space-y-4">
      <div class="flex justify-between items-center pb-4 border-b border-slate-800">
        <div>
          <h2 class="text-lg font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-triangle-exclamation text-rose-500"></i> ประวัติเหตุการณ์การล้ม (Fall Incidents Log)
          </h2>
          <p class="text-xs text-slate-400">บันทึกลง Self-hosted Database (SQLite) อย่างถาวร</p>
        </div>
        <button onclick="fetchFallEvents()" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium flex items-center gap-1.5 border border-slate-700/60">
          <i class="fa-solid fa-arrows-rotate"></i> รีเฟรช
        </button>
      </div>

      <div class="overflow-x-auto max-h-72 rounded-xl border border-slate-800">
        <table class="w-full text-left text-xs border-collapse">
          <thead class="bg-slate-900/90 text-slate-300 sticky top-0 z-10 uppercase tracking-wider text-[11px] border-b border-slate-800">
            <tr>
              <th class="py-3 px-4 font-semibold">#</th>
              <th class="py-3 px-4 font-semibold">เวลาที่เกิดเหตุ</th>
              <th class="py-3 px-4 font-semibold text-rose-400">แรงกระแทก (G)</th>
              <th class="py-3 px-4 font-semibold text-amber-400">อุณหภูมิ (°C)</th>
              <th class="py-3 px-4 font-semibold text-sky-400">ความชื้น (%RH)</th>
              <th class="py-3 px-4 font-semibold">การประเมินบริบท</th>
              <th class="py-3 px-4 font-semibold">สถานะ</th>
              <th class="py-3 px-4 font-semibold text-right">การจัดการ</th>
            </tr>
          </thead>
          <tbody id="fall-tbody" class="divide-y divide-slate-800/60 font-mono text-slate-300">
            <tr>
              <td colspan="8" class="py-8 text-center text-slate-500 font-sans">ยังไม่มีประวัติการล้ม (ปลอดภัย)</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 5. Environment History Table Section -->
    <div class="glass rounded-2xl p-6 shadow-xl space-y-4">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 pb-4 border-b border-slate-800">
        <div>
          <h2 class="text-lg font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-clock-rotate-left text-emerald-400"></i> ประวัติข้อมูลสภาพแวดล้อมย้อนหลัง (Telemetry History)
          </h2>
          <p class="text-xs text-slate-400">บันทึกข้อมูลย้อนหลังสูงสุด 100 รายการล่าสุดแบบเรียลไทม์</p>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="exportCSV()" class="px-3.5 py-2 rounded-xl bg-emerald-600/80 hover:bg-emerald-500 text-white text-xs font-medium flex items-center gap-1.5 transition-all shadow-md shadow-emerald-600/20">
            <i class="fa-solid fa-file-csv text-sm"></i> Export CSV
          </button>
          <button onclick="clearHistory()" class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-rose-600/80 text-slate-300 hover:text-white text-xs font-medium flex items-center gap-1.5 transition-all border border-slate-700/60">
            <i class="fa-solid fa-trash text-sm"></i> ล้างประวัติ
          </button>
        </div>
      </div>

      <div class="overflow-x-auto max-h-96 rounded-xl border border-slate-800">
        <table class="w-full text-left text-xs border-collapse">
          <thead class="bg-slate-900/90 text-slate-300 sticky top-0 z-10 uppercase tracking-wider text-[11px] border-b border-slate-800">
            <tr>
              <th class="py-3 px-4 font-semibold">#</th>
              <th class="py-3 px-4 font-semibold">เวลา (Timestamp)</th>
              <th class="py-3 px-4 font-semibold text-amber-400">อุณหภูมิ (°C)</th>
              <th class="py-3 px-4 font-semibold text-sky-400">ความชื้น (%RH)</th>
              <th class="py-3 px-4 font-semibold text-indigo-400">ความกดอากาศ (hPa)</th>
              <th class="py-3 px-4 font-semibold text-emerald-400">Wi-Fi (dBm)</th>
              <th class="py-3 px-4 font-semibold">สถานะ</th>
            </tr>
          </thead>
          <tbody id="history-tbody" class="divide-y divide-slate-800/60 font-mono text-slate-300">
            <tr>
              <td colspan="7" class="py-8 text-center text-slate-500 font-sans">กำลังรอข้อมูลสภาพแวดล้อมจากบอร์ด...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <!-- END TAB 1: DASHBOARD VIEW -->

    <!-- TAB 2: AI CHAT COMPANION VIEW -->
    <div id="view-chat" class="hidden max-w-3xl mx-auto flex flex-col h-[calc(100vh-140px)] min-h-[500px] glass rounded-3xl overflow-hidden border border-slate-800 shadow-2xl">
      <!-- Chat Header -->
      <div class="px-5 py-3.5 bg-slate-900/90 border-b border-slate-800 flex justify-between items-center flex-shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-emerald-500 to-indigo-600 text-white flex items-center justify-center text-xl shadow-lg shadow-emerald-500/20">
            🥒
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-sm font-bold text-white">น้องแตงกวา (Cucumber AI)</h2>
              <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            </div>
            <p class="text-[11px] text-slate-400">ผู้ช่วยสุขภาพ & บริบทแวดล้อม (Gemini 2.5 Flash)</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="clearChatMemory()" title="ล้างประวัติแชท" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-all">
            <i class="fa-solid fa-broom"></i> <span class="hidden sm:inline">ล้างแชท</span>
          </button>
        </div>
      </div>

      <!-- Quick Suggestion Pills -->
      <div class="px-4 py-2 bg-slate-950/70 border-b border-slate-800/80 overflow-x-auto whitespace-nowrap flex gap-2 text-xs flex-shrink-0">
        <button onclick="sendQuickPrompt('🌤️ สภาพอากาศในห้องตอนนี้เป็นอย่างไร ร้อนไหม')" class="px-3 py-1.5 rounded-full bg-slate-800/80 hover:bg-indigo-600/30 hover:border-indigo-500/50 text-slate-300 hover:text-white border border-slate-700/60 text-[11px] transition-all flex items-center gap-1.5 flex-shrink-0">
          🌤️ สภาพอากาศในห้อง
        </button>
        <button onclick="sendQuickPrompt('🥗 แนะนำมื้ออาหารเพื่อสุขภาพ 1 เมนู')" class="px-3 py-1.5 rounded-full bg-slate-800/80 hover:bg-emerald-600/30 hover:border-emerald-500/50 text-slate-300 hover:text-white border border-slate-700/60 text-[11px] transition-all flex items-center gap-1.5 flex-shrink-0">
          🥗 แนะนำเมนูอาหาร
        </button>
        <button onclick="sendQuickPrompt('🏃 แนะนำท่าออกกำลังกายยืดเหยียดป้องกันการล้ม')" class="px-3 py-1.5 rounded-full bg-slate-800/80 hover:bg-amber-600/30 hover:border-amber-500/50 text-slate-300 hover:text-white border border-slate-700/60 text-[11px] transition-all flex items-center gap-1.5 flex-shrink-0">
          🏃 ท่าป้องกันการล้ม
        </button>
        <button onclick="sendQuickPrompt('/status')" class="px-3 py-1.5 rounded-full bg-slate-800/80 hover:bg-purple-600/30 hover:border-purple-500/50 text-slate-300 hover:text-white border border-slate-700/60 text-[11px] transition-all flex items-center gap-1.5 flex-shrink-0">
          📊 เช็คเซนเซอร์สด
        </button>
      </div>

      <!-- Messages Box -->
      <div id="chat-messages-box" class="flex-1 p-4 sm:p-5 overflow-y-auto space-y-4 text-xs sm:text-sm">
        <!-- Default Welcome Assistant Message -->
        <div class="flex items-start gap-3">
          <div class="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center flex-shrink-0 font-bold text-sm">
            🥒
          </div>
          <div class="bg-slate-900 border border-slate-800/90 rounded-2xl rounded-tl-none p-3.5 max-w-[85%] text-slate-200 leading-relaxed shadow-md">
            สวัสดีครับ! ผมคือ <b>น้องแตงกวา AI</b> ผู้ช่วยสุขภาพส่วนตัวประจำอุปกรณ์ <b>Cucumber RS</b> ครับ 💚<br><br>
            คุณสามารถถามสภาพอากาศในห้อง, ปรึกษาเมนูอาหารสุขภาพ, หรือขอท่าออกกำลังกายป้องกันการล้มได้เลยครับ หรือจะกดปุ่มชิปแนะนำด้านบนก็ได้เช่นกันครับ!
          </div>
        </div>
      </div>

      <!-- Chat Input Area -->
      <div class="p-3 bg-slate-900/95 border-t border-slate-800 flex-shrink-0">
        <form id="chat-form" onsubmit="handleChatSubmit(event)" class="flex items-center gap-2">
          <input type="text" id="chat-input" placeholder="พิมพ์คุยกับน้องแตงกวา..." autocomplete="off" class="flex-1 px-4 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-xs sm:text-sm focus:border-indigo-500 focus:outline-none">
          <button type="submit" id="chat-send-btn" class="w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white flex items-center justify-center transition-all shadow-md shadow-indigo-600/30 flex-shrink-0">
            <i class="fa-solid fa-paper-plane text-xs sm:text-sm"></i>
          </button>
        </form>
      </div>
    </div>
    <!-- END TAB 2: AI CHAT COMPANION VIEW -->

  </main>

  <!-- BOTTOM NAVIGATION BAR (FIXED FOR TELEGRAM MINI APP) -->
  <nav class="fixed bottom-0 left-0 right-0 z-40 bg-slate-950/95 backdrop-blur-lg border-t border-slate-800/80 px-6 py-2 flex justify-around items-center max-w-md mx-auto sm:rounded-t-2xl sm:border-x shadow-2xl">
    <button onclick="switchTab('dashboard')" id="nav-btn-dashboard" class="flex flex-col items-center gap-1 text-emerald-400 transition-colors">
      <i class="fa-solid fa-chart-pie text-lg"></i>
      <span class="text-[11px] font-semibold">แดชบอร์ด</span>
    </button>
    <button onclick="switchTab('chat')" id="nav-btn-chat" class="flex flex-col items-center gap-1 text-slate-400 hover:text-indigo-400 transition-colors">
      <i class="fa-solid fa-comments text-lg"></i>
      <span class="text-[11px] font-semibold">น้องแตงกวา AI</span>
    </button>
  </nav>

  <!-- CONFIG MODAL -->
  <div id="config-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
    <div class="glass max-w-lg w-full rounded-3xl p-6 border border-slate-700 bg-slate-900 text-left space-y-4">
      <div class="flex justify-between items-center pb-3 border-b border-slate-800">
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <i class="fa-solid fa-sliders text-indigo-400"></i> ตั้งค่าระบบ AI & แจ้งเตือน Telegram
        </h3>
        <button onclick="closeConfigModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
      </div>

      <div class="space-y-3.5 text-xs">
        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-slate-300 font-medium">Google Gemini API Key:</label>
            <span class="text-[10px] text-emerald-400 font-semibold bg-emerald-500/10 px-2 py-0.5 rounded">Active (Gemini 2.5 Flash)</span>
          </div>
          <input type="password" id="gemini-key-input" placeholder="AIzaSy... / AQ.Ab8..." class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white font-mono focus:border-indigo-500 focus:outline-none">
          <span class="text-[10px] text-slate-500">ใช้สำหรับฟังก์ชันวิเคราะห์สรุป Daily Activity & Health Report ด้วย AI</span>
        </div>
        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-slate-300 font-medium">Telegram Bot Token:</label>
            <span class="text-[10px] text-sky-400 font-semibold bg-sky-500/10 px-2 py-0.5 rounded">@bercuncu_bot</span>
          </div>
          <input type="password" id="tg-token-input" placeholder="123456789:AAG..." class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-slate-300 font-medium">Telegram Chat ID (ตรวจจับอัตโนมัติ):</label>
            <a href="https://t.me/bercuncu_bot" target="_blank" class="text-[10px] text-sky-400 hover:underline flex items-center gap-1">
              <i class="fa-solid fa-arrow-up-right-from-square"></i> เปิดคุยกับ @bercuncu_bot
            </a>
          </div>
          <input type="text" id="tg-chat-input" placeholder="ระบบจะตรวจจับอัตโนมัติเมื่อกด /start ในบอท" class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white font-mono focus:border-indigo-500 focus:outline-none">
          <span class="text-[10px] text-slate-500 block mt-1">💡 หากยังไม่ขึ้น Chat ID ให้กดลิงก์ด้านบนแล้วส่งข้อความหรือกด <b>Start</b> ใน Telegram แล้วกดปุ่มทดสอบด้านล่างได้เลยครับ</span>
        </div>

        <!-- Bot Test Box -->
        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2">
          <div class="flex justify-between items-center">
            <span class="text-[11px] text-slate-300 font-medium">ทดสอบการส่งข้อความ Telegram:</span>
            <button onclick="testTelegramAlert()" id="test-tg-btn" class="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-medium text-xs flex items-center gap-1.5 transition-all">
              <i class="fa-solid fa-paper-plane"></i> ทดสอบส่งบอท
            </button>
          </div>
          <div id="tg-test-result" class="text-[11px] text-slate-400"></div>
        </div>
      </div>

      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="saveConfig()" class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs">บันทึกการตั้งค่า</button>
      </div>
    </div>
  </div>

  <script>
    // --- Audio Siren Synthesis (Web Audio API) ---
    let sirenInterval = null;
    function playSirenSound() {
      try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.type = 'sawtooth';
        let now = audioCtx.currentTime;
        osc.frequency.setValueAtTime(600, now);
        osc.frequency.linearRampToValueAtTime(1200, now + 0.35);
        osc.frequency.linearRampToValueAtTime(600, now + 0.7);
        gain.gain.setValueAtTime(0.4, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.9);
        osc.start(now);
        osc.stop(now + 0.9);
      } catch (e) {
        console.error("Audio error:", e);
      }
    }

    // --- Chart Setups ---
    const ctxEnv = document.getElementById('envChart').getContext('2d');
    const envData = {
      labels: [],
      datasets: [
        { label: 'อุณหภูมิ (°C)', borderColor: '#f59e0b', backgroundColor: 'rgba(245, 158, 11, 0.1)', data: [], yAxisID: 'yTemp', tension: 0.3, borderWidth: 2 },
        { label: 'ความชื้น (%RH)', borderColor: '#38bdf8', backgroundColor: 'rgba(56, 189, 248, 0.1)', data: [], yAxisID: 'yHum', tension: 0.3, borderWidth: 2 }
      ]
    };
    const envChart = new Chart(ctxEnv, {
      type: 'line',
      data: envData,
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
          yTemp: { type: 'linear', position: 'left', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#f59e0b' } },
          yHum: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, ticks: { color: '#38bdf8' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });

    const ctxAccel = document.getElementById('accelChart').getContext('2d');
    const accelData = {
      labels: [],
      datasets: [
        { label: 'Accel X (m/s²)', borderColor: '#ef4444', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 },
        { label: 'Accel Y (m/s²)', borderColor: '#22c55e', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 },
        { label: 'Accel Z (m/s²)', borderColor: '#3b82f6', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 }
      ]
    };
    const accelChart = new Chart(ctxAccel, {
      type: 'line',
      data: accelData,
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
          y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#cbd5e1' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });

    const ctxGyro = document.getElementById('gyroChart').getContext('2d');
    const gyroData = {
      labels: [],
      datasets: [
        { label: 'Gyro X (rad/s)', borderColor: '#f97316', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 },
        { label: 'Gyro Y (rad/s)', borderColor: '#a855f7', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 },
        { label: 'Gyro Z (rad/s)', borderColor: '#06b6d4', backgroundColor: 'transparent', data: [], tension: 0.25, borderWidth: 2 }
      ]
    };
    const gyroChart = new Chart(ctxGyro, {
      type: 'line',
      data: gyroData,
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
          y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#cbd5e1' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });

    function normalizeBar(val, minVal, maxVal) {
      let percent = ((val - minVal) / (maxVal - minVal)) * 100;
      return Math.min(Math.max(percent, 0), 100);
    }

    let globalHistory = [];
    let currentEmergencyId = null;

    // Check Emergency Status
    async function checkEmergency() {
      try {
        const res = await fetch('/api/emergency-status');
        if (!res.ok) return;
        const em = await res.json();
        const modal = document.getElementById('emergency-modal');

        if (em.active) {
          currentEmergencyId = em.id;
          document.getElementById('emergency-time').textContent = em.timestamp;
          document.getElementById('emergency-g').textContent = em.max_g + ' G';
          document.getElementById('emergency-temp').textContent = em.temperature + ' °C';
          document.getElementById('emergency-hum').textContent = em.humidity + ' %RH';
          document.getElementById('emergency-risk').textContent = em.risk;

          modal.classList.remove('hidden');

          if (!sirenInterval) {
            playSirenSound();
            sirenInterval = setInterval(playSirenSound, 1200);
          }
        } else {
          modal.classList.add('hidden');
          if (sirenInterval) {
            clearInterval(sirenInterval);
            sirenInterval = null;
          }
        }
      } catch (e) {
        console.error("Emergency check error:", e);
      }
    }

    async function resolveEmergency() {
      if (!currentEmergencyId) return;
      try {
        await fetch(`/api/fall-event/${currentEmergencyId}/resolve`, { method: 'POST' });
        document.getElementById('emergency-modal').classList.add('hidden');
        if (sirenInterval) {
          clearInterval(sirenInterval);
          sirenInterval = null;
        }
        fetchFallEvents();
      } catch (e) {
        console.error("Resolve error:", e);
      }
    }

    async function resolveSpecificEvent(id) {
      try {
        await fetch(`/api/fall-event/${id}/resolve`, { method: 'POST' });
        fetchFallEvents();
        checkEmergency();
      } catch (e) {
        console.error(e);
      }
    }

    async function fetchFallEvents() {
      try {
        const res = await fetch('/api/fall-events');
        if (!res.ok) return;
        const data = await res.json();
        const tbody = document.getElementById('fall-tbody');
        if (!data.events || data.events.length === 0) {
          tbody.innerHTML = '<tr><td colspan="8" class="py-8 text-center text-slate-500 font-sans">ยังไม่มีประวัติการล้ม (ปลอดภัย)</td></tr>';
          return;
        }
        let html = '';
        data.events.forEach(e => {
          let statusBadge = e.status === 'PENDING' 
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] bg-rose-500/20 text-rose-400 border border-rose-500/30 animate-pulse font-sans">รอดำเนินการ</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-400 font-sans">รับทราบแล้ว</span>';
          
          let resolveBtn = e.status === 'PENDING'
            ? `<button onclick="resolveSpecificEvent(${e.id})" class="px-2.5 py-1 rounded-lg bg-rose-600/80 hover:bg-rose-500 text-white text-[11px] font-sans">รับทราบ</button>`
            : '<span class="text-slate-500 font-sans text-[11px]">เสร็จสิ้น</span>';

          html += `
            <tr class="hover:bg-slate-900/40 transition-colors">
              <td class="py-2.5 px-4 text-slate-500">${e.id}</td>
              <td class="py-2.5 px-4 font-sans text-slate-300">${e.timestamp}</td>
              <td class="py-2.5 px-4 text-rose-400 font-bold">${e.max_g} G</td>
              <td class="py-2.5 px-4 text-amber-400">${e.temperature} °C</td>
              <td class="py-2.5 px-4 text-sky-400">${e.humidity} %RH</td>
              <td class="py-2.5 px-4 font-sans text-xs text-slate-300 max-w-xs truncate">${e.context_risk}</td>
              <td class="py-2.5 px-4">${statusBadge}</td>
              <td class="py-2.5 px-4 text-right">${resolveBtn}</td>
            </tr>
          `;
        });
        tbody.innerHTML = html;
      } catch (err) {
        console.error("Fetch fall events error:", err);
      }
    }

    function renderHistoryTable(historyList) {
      const tbody = document.getElementById('history-tbody');
      if (!historyList || historyList.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-500 font-sans">ยังไม่มีข้อมูลประวัติ</td></tr>';
        return;
      }
      let html = '';
      historyList.forEach((row, idx) => {
        let rssiBadge = 'text-emerald-400';
        if (row.rssi < -70) rssiBadge = 'text-amber-400';
        if (row.rssi < -80) rssiBadge = 'text-rose-400';

        html += `
          <tr class="hover:bg-slate-900/40 transition-colors">
            <td class="py-2.5 px-4 text-slate-500">${row.id || (idx + 1)}</td>
            <td class="py-2.5 px-4 text-slate-300 font-sans">${row.timestamp}</td>
            <td class="py-2.5 px-4 text-amber-400 font-bold">${row.temperature != null ? row.temperature.toFixed(2) : '--'}</td>
            <td class="py-2.5 px-4 text-sky-400 font-bold">${row.humidity != null ? row.humidity.toFixed(2) : '--'}</td>
            <td class="py-2.5 px-4 text-indigo-300">${row.pressure != null ? row.pressure.toFixed(2) : '--'}</td>
            <td class="py-2.5 px-4 ${rssiBadge} font-bold">${row.rssi != null ? row.rssi : '--'}</td>
            <td class="py-2.5 px-4 font-sans"><span class="px-2 py-0.5 rounded-full text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">ปกติ</span></td>
          </tr>
        `;
      });
      tbody.innerHTML = html;
    }

    function exportCSV() {
      if (!globalHistory || globalHistory.length === 0) {
        alert("ยังไม่มีข้อมูลประวัติสำหรับ Export ครับ");
        return;
      }
      let csvContent = "\\uFEFFID,Timestamp,Temperature (C),Humidity (%RH),Pressure (hPa),RSSI (dBm)\\n";
      globalHistory.forEach(row => {
        csvContent += `"${row.id}","${row.timestamp}","${row.temperature}","${row.humidity}","${row.pressure}","${row.rssi}"\\n`;
      });
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.setAttribute("href", url);
      link.setAttribute("download", `environment_history_${new Date().toISOString().slice(0,10)}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }

    async function clearHistory() {
      if (confirm("คุณต้องการล้างข้อมูลประวัติสภาพแวดล้อมทั้งหมดใช่หรือไม่?")) {
        try {
          await fetch('/api/history/clear', { method: 'POST' });
          globalHistory = [];
          renderHistoryTable([]);
        } catch (e) {
          console.error("Clear error:", e);
        }
      }
    }

    async function fetchSensorData() {
      try {
        const res = await fetch('/api/sensor-data');
        if (!res.ok) throw new Error('Network error');
        const data = await res.json();

        if (data.is_connected && data.last_updated) {
          document.getElementById('status-dot').className = 'w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse';
          document.getElementById('status-text').textContent = 'เชื่อมต่อแล้ว (Online)';

          // HTS221
          document.getElementById('temp-val').textContent = data.temperature;
          document.getElementById('hum-val').textContent = data.humidity;
          if (data.humidity > 70) {
            document.getElementById('hum-status').textContent = 'ความชื้นสูง';
          } else if (data.humidity < 40) {
            document.getElementById('hum-status').textContent = 'ความชื้นต่ำ';
          } else {
            document.getElementById('hum-status').textContent = 'สบายตัว (ปกติ)';
          }

          // BMP280
          document.getElementById('press-val').textContent = data.pressure;

          // WiFi RSSI
          document.getElementById('rssi-val').textContent = data.rssi;
          let rssiStatus = 'อ่อนมาก';
          if (data.rssi >= -50) rssiStatus = 'ดีมาก (Excellent)';
          else if (data.rssi >= -60) rssiStatus = 'ดี (Good)';
          else if (data.rssi >= -70) rssiStatus = 'ปานกลาง (Fair)';
          else if (data.rssi >= -80) rssiStatus = 'อ่อน (Weak)';
          document.getElementById('rssi-status').textContent = rssiStatus;

          // MPU-6050 Progress Bars
          if (data.accel) {
            document.getElementById('accel-x').textContent = data.accel.x;
            document.getElementById('accel-y').textContent = data.accel.y;
            document.getElementById('accel-z').textContent = data.accel.z;
            document.getElementById('accel-x-bar').style.width = normalizeBar(data.accel.x, -19.6, 19.6) + '%';
            document.getElementById('accel-y-bar').style.width = normalizeBar(data.accel.y, -19.6, 19.6) + '%';
            document.getElementById('accel-z-bar').style.width = normalizeBar(data.accel.z, -19.6, 19.6) + '%';
          }
          if (data.gyro) {
            document.getElementById('gyro-x').textContent = data.gyro.x;
            document.getElementById('gyro-y').textContent = data.gyro.y;
            document.getElementById('gyro-z').textContent = data.gyro.z;
            document.getElementById('gyro-x-bar').style.width = normalizeBar(data.gyro.x, -5, 5) + '%';
            document.getElementById('gyro-y-bar').style.width = normalizeBar(data.gyro.y, -5, 5) + '%';
            document.getElementById('gyro-z-bar').style.width = normalizeBar(data.gyro.z, -5, 5) + '%';
          }

          const nowTime = new Date().toLocaleTimeString();

          // 1. Env Chart
          if (envData.labels.length >= 25) {
            envData.labels.shift();
            envData.datasets[0].data.shift();
            envData.datasets[1].data.shift();
          }
          envData.labels.push(nowTime);
          envData.datasets[0].data.push(data.temperature);
          envData.datasets[1].data.push(data.humidity);
          envChart.update();

          // 2. Accel Chart
          if (data.accel) {
            if (accelData.labels.length >= 25) {
              accelData.labels.shift();
              accelData.datasets[0].data.shift();
              accelData.datasets[1].data.shift();
              accelData.datasets[2].data.shift();
            }
            accelData.labels.push(nowTime);
            accelData.datasets[0].data.push(data.accel.x);
            accelData.datasets[1].data.push(data.accel.y);
            accelData.datasets[2].data.push(data.accel.z);
            accelChart.update();
          }

          // 3. Gyro Chart
          if (data.gyro) {
            if (gyroData.labels.length >= 25) {
              gyroData.labels.shift();
              gyroData.datasets[0].data.shift();
              gyroData.datasets[1].data.shift();
              gyroData.datasets[2].data.shift();
            }
            gyroData.labels.push(nowTime);
            gyroData.datasets[0].data.push(data.gyro.x);
            gyroData.datasets[1].data.push(data.gyro.y);
            gyroData.datasets[2].data.push(data.gyro.z);
            gyroChart.update();
          }
        }
      } catch (err) {
        console.error("Error fetching sensor data:", err);
      }
    }

    async function fetchHistoryData() {
      try {
        const res = await fetch('/api/history');
        if (!res.ok) return;
        const data = await res.json();
        if (data.history) {
          globalHistory = data.history;
          renderHistoryTable(data.history);
        }
      } catch (e) {
        console.error("Error fetching history:", e);
      }
    }

    // Telegram Alert Test
    async function testTelegramAlert() {
      const btn = document.getElementById('test-tg-btn');
      const resDiv = document.getElementById('tg-test-result');
      const token = document.getElementById('tg-token-input').value.trim();
      const chatId = document.getElementById('tg-chat-input').value.trim();

      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> กำลังส่ง...';
      resDiv.innerHTML = '<span class="text-slate-400">กำลังติดต่อ Telegram Server...</span>';

      try {
        const res = await fetch('/api/telegram/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: token || null, chat_id: chatId || null })
        });
        const data = await res.json();
        if (data.status === 'success') {
          resDiv.innerHTML = `<span class="text-emerald-400 font-semibold"><i class="fa-solid fa-circle-check"></i> ส่งข้อความแจ้งเตือนสำเร็จแล้ว! (Chat ID: ${data.chat_id})</span>`;
          if (data.chat_id) {
            document.getElementById('tg-chat-input').value = data.chat_id;
            localStorage.setItem('tg_chat_id', data.chat_id);
          }
        } else if (data.status === 'waiting_chat') {
          resDiv.innerHTML = `<span class="text-amber-400"><i class="fa-solid fa-triangle-exclamation"></i> ยังไม่พบ Chat ID! กรุณาเปิดแอป Telegram แล้วกด Start ในบอท <a href="https://t.me/bercuncu_bot" target="_blank" class="underline font-bold text-sky-400">@bercuncu_bot</a> ก่อนครับ แล้วกดปุ่มนี้ใหม่อีกครั้ง</span>`;
        } else {
          resDiv.innerHTML = `<span class="text-rose-400"><i class="fa-solid fa-circle-xmark"></i> ผิดพลาด: ${data.message || data.detail || 'ไม่สามารถส่งได้'}</span>`;
        }
      } catch (e) {
        resDiv.innerHTML = `<span class="text-rose-400">เกิดข้อผิดพลาด: ${e.message}</span>`;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> ทดสอบส่งบอท';
      }
    }

    // AI Daily Summary Trigger
    async function generateAISummary() {
      const apiKey = localStorage.getItem('gemini_api_key') || '';
      const btn = document.getElementById('ai-generate-btn');
      const placeholder = document.getElementById('ai-placeholder');
      const content = document.getElementById('ai-content');

      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> กำลังสรุปข้อมูลด้วย Gemini...';
      placeholder.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-2xl text-indigo-400 block mb-2"></i> ระบบกำลังสรุปเหตุการณ์การล้มและบริบทแวดล้อมด้วย AI...';
      content.classList.add('hidden');

      try {
        const res = await fetch('/api/ai/daily-summary', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: apiKey || null })
        });
        const data = await res.json();
        if (res.ok) {
          placeholder.classList.add('hidden');
          content.classList.remove('hidden');
          content.innerHTML = marked.parse(data.summary);
        } else {
          placeholder.innerHTML = `<span class="text-rose-400">เกิดข้อผิดพลาด: ${data.detail || 'ไม่สามารถติดต่อ AI ได้'}</span>`;
        }
      } catch (e) {
        placeholder.innerHTML = `<span class="text-rose-400">เกิดข้อผิดพลาด: ${e.message}</span>`;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> สรุปเหตุการณ์การล้มด้วย AI';
      }
    }

    // Load Latest Summary if available
    async function loadLatestSummary() {
      try {
        const res = await fetch('/api/ai/daily-summary/latest');
        if (!res.ok) return;
        const data = await res.json();
        if (data && data.summary) {
          document.getElementById('ai-placeholder').classList.add('hidden');
          const content = document.getElementById('ai-content');
          content.classList.remove('hidden');
          content.innerHTML = marked.parse(data.summary);
        }
      } catch (e) {
        console.error(e);
      }
    }

    // Config Modal functions
    async function openConfigModal() {
      document.getElementById('gemini-key-input').value = localStorage.getItem('gemini_api_key') || '';
      document.getElementById('tg-token-input').value = localStorage.getItem('tg_bot_token') || '';
      
      let currentChatId = localStorage.getItem('tg_chat_id') || '';
      if (!currentChatId) {
        try {
          const res = await fetch('/api/config');
          const cfg = await res.json();
          if (cfg.telegram_chat_id) {
            currentChatId = cfg.telegram_chat_id;
            localStorage.setItem('tg_chat_id', currentChatId);
          }
        } catch (e) {}
      }
      document.getElementById('tg-chat-input').value = currentChatId;
      document.getElementById('tg-test-result').innerHTML = '';
      document.getElementById('config-modal').classList.remove('hidden');
    }

    function closeConfigModal() {
      document.getElementById('config-modal').classList.add('hidden');
    }

    function saveConfig() {
      localStorage.setItem('gemini_api_key', document.getElementById('gemini-key-input').value.trim());
      localStorage.setItem('tg_bot_token', document.getElementById('tg-token-input').value.trim());
      localStorage.setItem('tg_chat_id', document.getElementById('tg-chat-input').value.trim());
      alert("บันทึกการตั้งค่าเรียบร้อยแล้วครับ");
      closeConfigModal();
    }

    // --- TELEGRAM MINI APP (TMA) & CHAT INTEGRATION ---
    let currentChatId = localStorage.getItem('tg_chat_id') || 'web_user';
    let isChatHistoryLoaded = false;

    // Telegram WebApp SDK Initialization
    if (window.Telegram && window.Telegram.WebApp) {
      try {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
        
        // Extract Telegram user info
        const tgUser = Telegram.WebApp.initDataUnsafe?.user;
        if (tgUser && tgUser.id) {
          currentChatId = String(tgUser.id);
          localStorage.setItem('tg_chat_id', currentChatId);
          console.log("[TMA] Detected Telegram User ID:", currentChatId);
        }

        // Show TMA Badge
        const tmaBadge = document.getElementById('tma-badge');
        if (tmaBadge) tmaBadge.classList.remove('hidden');

        // Apply theme colors if available
        if (Telegram.WebApp.themeParams) {
          console.log("[TMA] Telegram theme params loaded:", Telegram.WebApp.themeParams);
        }
      } catch (e) {
        console.warn("[TMA] WebApp init error:", e);
      }
    }

    // Switch between Dashboard and AI Chat
    function switchTab(tab) {
      const vDash = document.getElementById('view-dashboard');
      const vChat = document.getElementById('view-chat');
      const btnDash = document.getElementById('nav-btn-dashboard');
      const btnChat = document.getElementById('nav-btn-chat');

      if (window.Telegram?.WebApp?.HapticFeedback) {
        Telegram.WebApp.HapticFeedback.selectionChanged();
      }

      if (tab === 'dashboard') {
        vDash.classList.remove('hidden');
        vChat.classList.add('hidden');
        btnDash.className = 'flex flex-col items-center gap-1 text-emerald-400 transition-colors';
        btnChat.className = 'flex flex-col items-center gap-1 text-slate-400 hover:text-indigo-400 transition-colors';
      } else {
        vDash.classList.add('hidden');
        vChat.classList.remove('hidden');
        btnChat.className = 'flex flex-col items-center gap-1 text-indigo-400 transition-colors';
        btnDash.className = 'flex flex-col items-center gap-1 text-slate-400 hover:text-emerald-400 transition-colors';
        
        if (!isChatHistoryLoaded) {
          loadChatHistory();
        }
        scrollChatToBottom();
        setTimeout(() => {
          const input = document.getElementById('chat-input');
          if (input) input.focus();
        }, 150);
      }
    }

    function scrollChatToBottom() {
      const box = document.getElementById('chat-messages-box');
      if (box) box.scrollTop = box.scrollHeight;
    }

    function escapeHtml(text) {
      const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'};
      return text.replace(/[&<>"']/g, m => map[m]);
    }

    function appendUserBubble(text) {
      const box = document.getElementById('chat-messages-box');
      const div = document.createElement('div');
      div.className = 'flex items-start justify-end gap-3';
      div.innerHTML = `
        <div class="bg-indigo-600 text-white rounded-2xl rounded-tr-none p-3.5 max-w-[85%] text-xs sm:text-sm leading-relaxed shadow-md font-sans">
          ${escapeHtml(text)}
        </div>
        <div class="w-8 h-8 rounded-xl bg-indigo-500/30 text-indigo-300 flex items-center justify-center flex-shrink-0 font-bold text-xs">
          <i class="fa-solid fa-user"></i>
        </div>
      `;
      box.appendChild(div);
      scrollChatToBottom();
    }

    function appendAssistantBubble(contentHtml, id = null) {
      const box = document.getElementById('chat-messages-box');
      const div = document.createElement('div');
      div.className = 'flex items-start gap-3';
      if (id) div.id = id;
      div.innerHTML = `
        <div class="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center flex-shrink-0 font-bold text-sm">
          🥒
        </div>
        <div class="bg-slate-900 border border-slate-800/90 rounded-2xl rounded-tl-none p-3.5 max-w-[85%] text-slate-200 leading-relaxed shadow-md prose prose-invert max-w-none text-xs sm:text-sm">
          ${contentHtml}
        </div>
      `;
      box.appendChild(div);
      scrollChatToBottom();
      return div;
    }

    async function sendChatMessage(overrideText = null) {
      const input = document.getElementById('chat-input');
      const text = (overrideText || (input ? input.value : '')).trim();
      if (!text) return;
      if (!overrideText && input) input.value = '';

      appendUserBubble(text);

      const tempId = 'typing-' + Date.now();
      appendAssistantBubble('<span class="text-slate-400 italic flex items-center gap-1.5"><i class="fa-solid fa-spinner fa-spin"></i> น้องแตงกวากำลังคิด...</span>', tempId);

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: currentChatId, message: text })
        });
        const data = await res.json();
        const typingElem = document.getElementById(tempId);
        if (typingElem) typingElem.remove();

        if (data.status === 'success') {
          appendAssistantBubble(marked.parse(data.reply));
          if (window.Telegram?.WebApp?.HapticFeedback) {
            Telegram.WebApp.HapticFeedback.notificationOccurred('success');
          }
        } else {
          appendAssistantBubble(`<span class="text-rose-400">เกิดข้อผิดพลาด: ${data.message || 'ไม่สามารถติดต่อ AI ได้'}</span>`);
        }
      } catch (e) {
        const typingElem = document.getElementById(tempId);
        if (typingElem) typingElem.remove();
        appendAssistantBubble(`<span class="text-rose-400">เกิดข้อผิดพลาดในการส่งข้อความ: ${e.message}</span>`);
      }
    }

    function handleChatSubmit(e) {
      e.preventDefault();
      sendChatMessage();
    }

    function sendQuickPrompt(prompt) {
      sendChatMessage(prompt);
    }

    async function clearChatMemory() {
      if (confirm("ต้องการล้างประวัติการสนทนาเพื่อเริ่มหัวข้อใหม่ใช่หรือไม่?")) {
        await sendChatMessage("/clear");
      }
    }

    async function loadChatHistory() {
      try {
        const res = await fetch(`/api/chat/history?chat_id=${encodeURIComponent(currentChatId)}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'success' && data.history && data.history.length > 0) {
          const box = document.getElementById('chat-messages-box');
          box.innerHTML = ''; // Clear default welcome to render history
          data.history.forEach(item => {
            if (item.role === 'user') {
              appendUserBubble(item.text);
            } else {
              appendAssistantBubble(marked.parse(item.text));
            }
          });
          isChatHistoryLoaded = true;
          scrollChatToBottom();
        }
      } catch (e) {
        console.warn("Failed to load history:", e);
      }
    }

    // Polling Intervals
    setInterval(fetchSensorData, 1000);
    setInterval(fetchHistoryData, 3000);
    setInterval(fetchFallEvents, 2000);
    setInterval(checkEmergency, 500); // ตรวจสอบเหตุฉุกเฉินทุก 500ms แบบ Real-time ตอบสนองไว

    fetchSensorData();
    fetchHistoryData();
    fetchFallEvents();
    checkEmergency();
    loadLatestSummary();
  </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)
