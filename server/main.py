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
from models import FallEvent, Telemetry, DailySummary, BANGKOK_TZ, get_bangkok_now_str, get_bangkok_date_str
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
    "motion": None,      # summary of all 50 Hz samples since the previous telemetry post
    "seq": 0,            # increments on every telemetry post so clients can detect new samples
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
        now_bkk = datetime.now(BANGKOK_TZ)
        today_str = now_bkk.strftime("%Y-%m-%d")
        yesterday_str = (now_bkk - timedelta(days=1)).strftime("%Y-%m-%d")

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
        today_str = get_bangkok_date_str()
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
            "date": get_bangkok_date_str(),
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
    language: str = "th"

@app.post("/api/chat")
async def chat_with_ai(body: ChatMessageRequest):
    cid = body.chat_id or "web_user"
    reply = await tg_bot_service.ask_gemini(cid, body.message, language=body.language)
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

class MotionSummary(BaseModel):
    """Min/avg/max of every 50 Hz motion sample the board took since its previous post."""
    g_avg: float = 0.0
    g_min: float = 0.0
    g_max: float = 0.0
    rot_avg: float = 0.0
    rot_max: float = 0.0
    samples: int = 0

class SensorPayload(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    rssi: Optional[int] = None
    accel: Optional[AccelData] = None
    gyro: Optional[GyroData] = None
    motion: Optional[MotionSummary] = None

class FallEventPayload(BaseModel):
    max_g: float
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None

class AISummaryRequest(BaseModel):
    api_key: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    language: Optional[str] = None  # "en" -> English report for the caregiver web dashboard

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
async def update_tunnel_url(body: TunnelUrlPayload, x_test_mode: Optional[str] = Header(None)):
    global TUNNEL_PUBLIC_URL
    new_url = body.url.strip().rstrip("/")
    if not (new_url.startswith("http://") or new_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid URL: must start with http:// or https://")

    is_test = x_test_mode and x_test_mode.lower() in ("true", "1", "yes")
    if is_test:
        print(f"[Tunnel Sync] 🧪 (TEST MODE) Validation passed for test URL: {new_url}. Skiping .env and setChatMenuButton modification.")
        return {"status": "success", "message": "Test URL validated (Test Mode - no config altered)", "tunnel_url": new_url}

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
    if payload.motion is not None:
        m = payload.motion
        latest_data["motion"] = {
            "g_avg": round(m.g_avg, 3),
            "g_min": round(m.g_min, 3),
            "g_max": round(m.g_max, 3),
            "rot_avg": round(m.rot_avg, 3),
            "rot_max": round(m.rot_max, 3),
            "samples": m.samples,
        }
    else:
        latest_data["motion"] = None
    latest_data["seq"] += 1

    timestamp = get_bangkok_now_str()
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
async def receive_fall_event(payload: FallEventPayload, db: Session = Depends(get_db), x_test_mode: Optional[str] = Header(None)):
    global active_emergency
    timestamp = get_bangkok_now_str()
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

    # Save to SQLite (automatically routed to test_fall.db if X-Test-Mode is set)
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

    is_test = x_test_mode and x_test_mode.lower() in ("true", "1", "yes")
    if not is_test:
        # Dispatch Real Telegram Alert
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
    else:
        print(f"[Fall Alert] 🧪 (TEST MODE - X-Test-Mode) Stored in test_fall.db, skipped real Telegram broadcast.")

    return {"status": "success", "event_id": event.id, "risk": risk_level}

@app.get("/api/sensor-data")
async def get_sensor_data():
    global latest_data
    if latest_data.get("last_updated"):
        try:
            last_dt = datetime.strptime(latest_data["last_updated"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BANGKOK_TZ)
            diff_sec = (datetime.now(BANGKOK_TZ) - last_dt).total_seconds()
            if diff_sec > 15:
                latest_data["is_connected"] = False
        except Exception:
            pass
    else:
        latest_data["is_connected"] = False
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
    is_en = (body.language or "th").lower().startswith("en")
    if not api_key:
        detail = ("Please provide a Google Gemini API key to use this feature."
                  if is_en else "กรุณาระบุ Google Gemini API Key เพื่อใช้งานฟังก์ชันนี้ครับ")
        raise HTTPException(status_code=400, detail=detail)

    today_str = get_bangkok_date_str()
    
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

    if is_en:
        prompt = f"""
You are an incident-reporting assistant for the Cucumber RS wearable fall-detection system.
The reader is a CAREGIVER. Write a short, to-the-point report in ENGLISH covering only today's fall incidents
and the environmental context at the time of each incident. The incident data below may be written in Thai - translate it.
Do not add a long introduction or closing, do not analyse general routines, and do not give lengthy medical advice.

Incident data:
{incident_data}

Response format (keep it brief):
- If falls were detected, use short bullet points:
  • 🚨 **Fall incident:** time, impact force (G) and severity
  • 🌡️ **Environment at the time:** temperature, humidity and any environmental risk factor (e.g. high humidity = slippery bathroom floor, heat = risk of fainting)
  • ⚠️ **Caregiver action:** one short sentence on what the caregiver should do now
- If no falls were detected:
  1-2 lines stating that no falls were detected today, the wearer is safe, and the environment is within normal range.
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
            prefix = "Gemini API request failed" if is_en else "เกิดข้อผิดพลาดในการเรียก Gemini API"
            raise HTTPException(status_code=500, detail=f"{prefix}: {str(e2)}")

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

DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    """Caregiver dashboard (also served as the Telegram Mini App). Read per request so edits show up without a restart."""
    with open(DASHBOARD_HTML_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
