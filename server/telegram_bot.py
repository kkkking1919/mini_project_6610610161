import asyncio
import httpx
import json
from typing import Dict, List, Optional, Callable
from datetime import datetime

class TelegramBotService:
    def __init__(
        self,
        token: str,
        gemini_api_key: str,
        get_sensor_data_fn: Callable[[], dict],
        get_emergency_status_fn: Callable[[], dict],
        get_fall_history_fn: Optional[Callable[[], dict]] = None,
        get_lifestyle_summary_fn: Optional[Callable[[], dict]] = None,
        app_url: str = "https://broken-enter-recommendations-classroom.trycloudflare.com",
        on_chat_id_discovered: Optional[Callable[[str], None]] = None
    ):
        self.token = token
        self.gemini_api_key = gemini_api_key
        self.get_sensor_data = get_sensor_data_fn
        self.get_emergency_status = get_emergency_status_fn
        self.get_fall_history = get_fall_history_fn or (lambda: {"today_count": 0, "today_incidents": [], "yesterday_count": 0})
        self.get_lifestyle_summary = get_lifestyle_summary_fn or (lambda: {"score": 85, "avg_temp": "--", "avg_hum": "--"})
        self.app_url = app_url
        self.on_chat_id_discovered = on_chat_id_discovered
        self.chat_histories: Dict[str, List[dict]] = {}
        self.last_update_id: int = 0
        self.is_running: bool = False
        self.polling_task: Optional[asyncio.Task] = None

    def start(self):
        """Start the async long polling loop."""
        if not self.token:
            print("[TelegramBot] Token not provided, skipping polling.")
            return
        if self.is_running:
            return
        self.is_running = True
        self.polling_task = asyncio.create_task(self._poll_loop())
        print(f"[TelegramBot] Long-polling worker started for bot @bercuncu_bot.")
        # Setup persistent menu button
        if self.app_url:
            asyncio.create_task(self.setup_menu_button(self.app_url))

    def stop(self):
        """Stop the background polling loop."""
        self.is_running = False
        if self.polling_task:
            self.polling_task.cancel()
            self.polling_task = None
            print("[TelegramBot] Polling worker stopped.")

    async def setup_menu_button(self, app_url: str):
        """Configure Telegram persistent bottom menu button to open Mini App."""
        self.app_url = app_url
        if not self.token or not app_url:
            return
        url = f"https://api.telegram.org/bot{self.token}/setChatMenuButton"
        payload = {
            "menu_button": {
                "type": "web_app",
                "text": "📱 เปิดแอปสุขภาพ",
                "web_app": {
                    "url": app_url
                }
            }
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload)
                data = res.json()
                if data.get("ok"):
                    print(f"[TelegramBot] Persistent Menu Button registered successfully -> {app_url}")
                else:
                    print(f"[TelegramBot] setChatMenuButton error: {data}")
        except Exception as e:
            print(f"[TelegramBot] setChatMenuButton exception: {e}")

    async def _poll_loop(self):
        """Continuous long-polling loop with getUpdates."""
        await asyncio.sleep(2.0)  # Grace period on startup
        async with httpx.AsyncClient(timeout=35.0) as client:
            while self.is_running:
                try:
                    url = f"https://api.telegram.org/bot{self.token}/getUpdates"
                    params = {
                        "offset": self.last_update_id + 1 if self.last_update_id else 0,
                        "timeout": 20
                    }
                    res = await client.get(url, params=params)
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("ok") and data.get("result"):
                            for update in data["result"]:
                                self.last_update_id = max(self.last_update_id, update["update_id"])
                                if "message" in update and "text" in update["message"]:
                                    asyncio.create_task(self._handle_message(update["message"]))
                    elif res.status_code == 409:
                        await asyncio.sleep(5.0)
                    else:
                        await asyncio.sleep(2.0)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(3.0)

    async def send_typing_action(self, chat_id: str):
        """Send 'typing' indicator to the chat."""
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendChatAction"
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json={"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass

    async def send_reply(self, chat_id: str, text: str, reply_markup: Optional[dict] = None):
        """Send a message back to the user with Markdown fallback."""
        if not text:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        if len(text) > 4000:
            text = text[:3990] + "\n..."

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                res_data = res.json()
                if not res_data.get("ok"):
                    # Fallback to plain text if Markdown parsing fails
                    payload["parse_mode"] = None
                    await client.post(url, json=payload)
        except Exception as e:
            print(f"[TelegramBot] Send error: {e}")

    async def _handle_message(self, message: dict):
        """Process an incoming message from Telegram."""
        chat_id = str(message["chat"]["id"])
        user_text = message.get("text", "").strip()
        from_user = message.get("from", {})
        user_name = from_user.get("first_name") or "คุณ"

        # Update detected chat id
        if self.on_chat_id_discovered:
            self.on_chat_id_discovered(chat_id)

        # Trigger typing indicator
        asyncio.create_task(self.send_typing_action(chat_id))

        cmd = user_text.lower().split()[0] if user_text else ""

        if cmd in ["/start", "/help", "/app"]:
            await self._cmd_help(chat_id, user_name)
            return

        # Use unified ask_gemini method for all queries and commands
        ai_reply = await self.ask_gemini(chat_id, user_text)
        await self.send_reply(chat_id, ai_reply)

    def get_chat_history(self, chat_id: str) -> List[dict]:
        """Return memory turns for a specific chat_id."""
        return self.chat_histories.get(chat_id, [])

    async def ask_gemini(self, chat_id: str, user_text: str, language: str = "th") -> str:
        """Unified method for processing text through commands and Gemini with live context.

        language="en" is used by the English caregiver web dashboard: replies come back in
        English from a neutral "Care Assistant" persona. Telegram keeps the default ("th").
        """
        cmd = user_text.lower().split()[0] if user_text else ""
        is_en = (language or "th").lower().startswith("en")

        if is_en:
            if cmd == "/clear":
                self.chat_histories[chat_id] = []
                return "🧹 Conversation cleared. You can start a new question now."
            sensor_context = self._format_sensor_context(self.get_sensor_data(), self.get_emergency_status())
            return await self._call_gemini_chat(chat_id, user_text, user_text, sensor_context, language="en")

        if cmd == "/status":
            return self._build_status_text()

        if cmd in ["/falls", "/fall"]:
            return self._build_falls_report()

        if cmd in ["/summary", "/daily", "/lifestyle"]:
            return self._build_lifestyle_report()

        if cmd == "/clear":
            self.chat_histories[chat_id] = []
            return "🧹 *ล้างประวัติการสนทนาเรียบร้อยแล้วครับ!*\nคุณสามารถเริ่มต้นถามคำถามใหม่ได้ทันทีครับ"

        # Prepare Sensor Context for Gemini
        sensor_data = self.get_sensor_data()
        emergency_data = self.get_emergency_status()
        sensor_context = self._format_sensor_context(sensor_data, emergency_data)

        # Pre-process built-in command shortcuts
        prompt_override = None
        if cmd == "/weather":
            prompt_override = f"ผู้ใช้ต้องการทราบรายงานสภาพอากาศและสภาพแวดล้อมในห้องขณะนี้ กรุณานำข้อมูลเซนเซอร์จริงต่อไปนี้มารายงานแบบน่าฟัง: {sensor_context} พร้อมแนะนำความสบายของบรรยากาศและการดื่มน้ำสั้นๆ"
        elif cmd == "/food":
            prompt_override = "ผู้ใช้ต้องการคำแนะนำมื้ออาหารเพื่อสุขภาพ (โภชนาการสมดุล ย่อยง่าย เหมาะกับสุขภาพร่างกาย) กรุณาแนะนำ 1-2 เมนูอย่างน่ารับประทานพร้อมประโยชน์"
        elif cmd == "/exercise":
            prompt_override = "ผู้ใช้ต้องการคำแนะนำท่าออกกำลังกายหรือการยืดเหยียดที่ปลอดภัยและช่วยป้องกันการล้ม (เน้นการทรงตัว ยืดเหยียดกล้ามเนื้อ ไม่หักโหม) กรุณาแนะนำ 2 ท่าแบบเป็นขั้นตอนเข้าใจง่าย"

        query_text = prompt_override or user_text
        return await self._call_gemini_chat(chat_id, query_text, user_text, sensor_context)

    def _build_status_text(self) -> str:
        s = self.get_sensor_data()
        em = self.get_emergency_status()
        is_conn = s.get("is_connected", False)
        temp = s.get("temperature", "--") if is_conn else "--"
        hum = s.get("humidity", "--") if is_conn else "--"
        press = s.get("pressure", "--") if is_conn else "--"
        rssi = s.get("rssi", "--") if is_conn else "--"
        updated = s.get("last_updated") or "รอการเชื่อมต่อ..."
        is_em = em.get("active", False)
        em_badge = "🚨 *ตรวจพบการล้ม (EMERGENCY ACTIVE!)*" if is_em else "✅ *ปลอดภัย (ปกติ)*"
        conn_badge = "🟢 *เชื่อมต่ออยู่ (Online)*" if is_conn else "🔴 *ขาดการเชื่อมต่อ (Offline)*"

        return (
            f"📊 *สถานะเซนเซอร์บนบอร์ด Cucumber RS*\n\n"
            f"📡 *สถานะบอร์ด:* {conn_badge}\n"
            f"🌡️ *อุณหภูมิ (HTS221):* `{temp} °C`\n"
            f"💧 *ความชื้นสัมพัทธ์ (HTS221):* `{hum} %RH`\n"
            f"🌪️ *ความกดอากาศ (BMP280):* `{press} hPa`\n"
            f"📶 *สัญญาณ Wi-Fi:* `{rssi} dBm`\n"
            f"⏱️ *อัปเดตล่าสุด:* `{updated}`\n"
            f"🛡️ *สถานะความปลอดภัย:* {em_badge}\n\n"
            f"💡 *เคล็ดลับ:* คุณสามารถเปิดหน้าแดชบอร์ดเต็มจอได้ที่ปุ่มเมนูด้านล่างครับ"
        )

    def _build_falls_report(self) -> str:
        """Concise fall incident report directly from SQLite database."""
        f_data = self.get_fall_history()
        today_count = f_data.get("today_count", 0)
        incidents = f_data.get("today_incidents", [])
        yesterday_count = f_data.get("yesterday_count", 0)

        if today_count == 0:
            msg = (
                "🛡️ *รายงานสถิติการล้มประจำวันนี้*\n\n"
                "✅ *วันนี้ไม่พบเหตุการณ์การล้ม* ผู้สวมใส่อยู่ในสภาวะปลอดภัย ปกติดีครับ"
            )
            if yesterday_count > 0:
                msg += f"\n*(สถิติย้อนหลัง: เมื่อวานพบการล้ม {yesterday_count} ครั้ง)*"
            return msg

        msg = f"🚨 *รายงานสถิติการล้มวันนี้ (พบทั้งหมด {today_count} ครั้ง)*\n\n"
        for idx, inc in enumerate(incidents, 1):
            time = inc.get("time", "--:--")
            g = inc.get("max_g", 0.0)
            risk = inc.get("risk", "ปานกลาง")
            msg += f"• *ครั้งที่ {idx}* ({time} น.): แรงกระแทก `{g} G` | {risk}\n"

        if yesterday_count > 0:
            msg += f"\n*(สถิติย้อนหลัง: เมื่อวานพบการล้ม {yesterday_count} ครั้ง)*\n"
        msg += "\n⚠️ *คำแนะนำ:* กรุณาตรวจสอบผู้สวมใส่และบริเวณรอบๆ ทันทีครับ"
        return msg

    def _build_lifestyle_report(self) -> str:
        """Concise daily lifestyle & activity report directly from SQLite database."""
        ls = self.get_lifestyle_summary()
        f_data = self.get_fall_history()
        today_count = f_data.get("today_count", 0)
        score = ls.get("score", 85)
        avg_t = ls.get("avg_temp", "--")
        avg_h = ls.get("avg_hum", "--")
        date = ls.get("date", datetime.now().strftime("%Y-%m-%d"))

        score_badge = "🟢 ยอดเยี่ยม" if score >= 80 else ("🟡 ควรระวัง" if score >= 50 else "🔴 เสี่ยงสูง")

        overview_text = (
            "วันนี้ไม่พบการล้ม สภาพแวดล้อมในห้องเหมาะสม แนะนำทำกิจวัตรตามปกติและดื่มน้ำสม่ำเสมอครับ"
            if today_count == 0 else
            f"วันนี้พบการล้ม {today_count} ครั้ง สภาพแวดล้อมอาจมีปัจจัยเสี่ยง แนะนำให้ผู้ดูแลสังเกตการเดินและประเมินอาการอย่างใกล้ชิดครับ"
        )

        return (
            f"📋 *สรุปการใช้ชีวิตและสุขภาวะประจำวัน ({date})*\n\n"
            f"🏅 *คะแนนสุขภาวะ (Activity Score):* `{score}/100` [{score_badge}]\n"
            f"🚨 *การล้มวันนี้:* `{today_count} ครั้ง`\n"
            f"🌡️ *สภาพแวดล้อมเฉลี่ยในห้อง:* อุณหภูมิ `{avg_t} °C` | ความชื้น `{avg_h} %RH`\n\n"
            f"💡 *สรุปภาพรวม:* {overview_text}"
        )

    def _format_sensor_context(self, s: dict, em: dict) -> str:
        """Create a compact live sensor, fall incident, and lifestyle context for Gemini AI."""
        is_conn = s.get("is_connected", False)
        temp = s.get("temperature", "--")
        hum = s.get("humidity", "--")
        press = s.get("pressure", "--")
        last_updated = s.get("last_updated")
        em_status = "🚨 เกิดเหตุการณ์ล้ม (กำลังรอความช่วยเหลือ)" if em.get("active") else "✅ ปกติ ปลอดภัย"

        if not is_conn or not last_updated:
            sensor_str = "สถานะเซนเซอร์สด: ⚠️ บอร์ด Cucumber RS ขาดการเชื่อมต่อ (Offline เกิน 15 วินาที) หากผู้ใช้ถามเรื่องสภาพห้องปัจจุบัน ให้แจ้งผู้ใช้ว่าบอร์ดออฟไลน์อยู่"
        else:
            sensor_str = f"ข้อมูลเซนเซอร์สดขณะนี้: อุณหภูมิ {temp} °C (HTS221), ความชื้น {hum} %RH (HTS221), ความกดอากาศ {press} hPa (BMP280), อัปเดตล่าสุด {last_updated}"

        # Fall incident stats from SQLite
        f_data = self.get_fall_history()
        today_count = f_data.get("today_count", 0)
        incidents = f_data.get("today_incidents", [])
        yesterday_count = f_data.get("yesterday_count", 0)

        if today_count == 0:
            falls_str = "สถิติการล้มวันนี้: วันนี้ยังไม่มีบันทึกการล้ม (ล้ม 0 ครั้ง ปลอดภัย)"
        else:
            incident_lines = []
            for idx, inc in enumerate(incidents, 1):
                incident_lines.append(f"  - ครั้งที่ {idx} เวลา {inc['time']} น.: แรงกระแทก {inc['max_g']}G, ระดับความรุนแรง/บริบท: {inc['risk']}")
            falls_str = f"สถิติการล้มวันนี้: พบการล้มทั้งหมด {today_count} ครั้ง ได้แก่:\n" + "\n".join(incident_lines)

        recent_stats_str = f"สถิติย้อนหลัง: เมื่อวานพบการล้ม {yesterday_count} ครั้ง"

        # Lifestyle & activity stats from SQLite
        ls = self.get_lifestyle_summary()
        lifestyle_str = (
            f"ข้อมูลสุขภาวะและการใช้ชีวิตวันนี้: คะแนน Activity Score = {ls.get('score', 85)}/100, "
            f"อุณหภูมิห้องเฉลี่ยทั้งวัน = {ls.get('avg_temp', '--')} °C, ความชื้นเฉลี่ยทั้งวัน = {ls.get('avg_hum', '--')} %RH, "
            f"จำนวนตัวอย่าง Telemetry ทั้งวัน = {ls.get('total_telemetry_samples', 0)} ครั้ง"
        )

        return (
            f"1. {sensor_str}\n"
            f"   สถานะความปลอดภัย: {em_status}\n\n"
            f"2. {falls_str}\n"
            f"   {recent_stats_str}\n\n"
            f"3. {lifestyle_str}"
        )

    async def _cmd_help(self, chat_id: str, user_name: str):
        msg = (
            f"🥒 *สวัสดีครับคุณ {user_name}! ผมคือ น้องแตงกวา (Cucumber AI)*\n"
            f"ผู้ช่วยอัจฉริยะด้านสุขภาพและบริบทแวดล้อม ประจำบอร์ด *Cucumber RS*\n\n"
            f"📱 *ใหม่! คุณสามารถเปิด Telegram Mini App ได้ทันที*\n"
            f"เพื่อดูแดชบอร์ดเซนเซอร์สด กราฟความเคลื่อนไหว และแชทคุยกับผมได้ในหน้าต่างเดียว ไม่ต้องสลับเบราว์เซอร์ครับ!\n\n"
            f"💬 *หรือพิมพ์คุยในช่องแชทนี้ได้เลย:*\n"
            f"• 🚨 *\"วันนี้ล้มทั้งหมดกี่ครั้ง รุนแรงแค่ไหน\"*\n"
            f"• 📋 *\"การใช้ชีวิตและสุขภาพประจำวันวันนี้เป็นอย่างไร\"*\n"
            f"• 🌡️ *\"สภาพอากาศในห้องตอนนี้เป็นอย่างไร ร้อนไหม\"*\n"
            f"• 🏃 *\"มีท่าออกกำลังกายหรือยืดเหยียดป้องกันการล้มไหม\"*\n\n"
            f"⚡ *คำสั่งลัด:* /status, /falls, /summary, /weather, /food, /exercise, /clear"
        )
        reply_markup = None
        if self.app_url:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 เปิด Cucumber RS Mini App",
                            "web_app": {"url": self.app_url}
                        }
                    ]
                ]
            }
        await self.send_reply(chat_id, msg, reply_markup=reply_markup)

    async def _call_gemini_chat(self, chat_id: str, query_for_ai: str, original_user_text: str, sensor_context: str, language: str = "th") -> str:
        """Call Google Gemini 2.5 Flash with multi-turn memory and live sensor context."""
        is_en = (language or "th").lower().startswith("en")
        if not self.gemini_api_key:
            return "Sorry, the Google Gemini API key has not been configured." if is_en else "ขออภัยครับ ยังไม่ได้ตั้งค่า Google Gemini API Key ในระบบ"

        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        history = self.chat_histories[chat_id]

        system_instruction = f"""คุณคือ "น้องแตงกวา (Cucumber AI)" ผู้ช่วยอัจฉริยะด้านสุขภาพ กายภาพบำบัด และบริบทสิ่งแวดล้อม ประจำอุปกรณ์สวมใส่ Cucumber RS
บุคลิกของคุณ: อบอุ่น สุภาพ ใส่ใจ เป็นมิตร ให้คำปรึกษาที่เข้าใจง่าย น่าเชื่อถือ ตรงไปตรงมา

ข้อมูลเซนเซอร์สด สถิติการล้มจริง และข้อมูลการใช้ชีวิตประจำวันจากฐานข้อมูล:
{sensor_context}

แนวทางการตอบคำถาม:
1. ด้านประวัติการล้มและความรุนแรง:
   - หากผู้ใช้ถามว่า "วันนี้ล้มกี่ครั้ง", "มีคนล้มไหม", "ประวัติการล้ม", "การล้มรุนแรงแค่ไหน", "กี่โมง":
     ให้นำข้อมูลจริงจาก "สถิติการล้มวันนี้" ด้านบนมาตอบผู้ใช้โดยตรง
     * ข้อกำหนดสำคัญ: "รายงานแบบสรุปย่อ สั้น กระชับ ตรงประเด็น (1-2 บรรทัด)" ระบุจำนวนครั้ง เวลาที่เกิด และระดับความรุนแรง (แรงกระแทก G) ชัดเจน ไม่เวิ่นเว้อ
     - หากวันนี้พบการล้ม: แจ้งจำนวนครั้ง เวลา และระดับแรงกระแทก G พร้อมให้คำแนะนำตรวจดูผู้สวมใส่
     - หากวันนี้ไม่พบการล้ม (0 ครั้ง): แจ้งสั้นๆ ว่า วันนี้ปลอดภัยดี ไม่พบประวัติการล้มครับ
     - หากผู้ใช้ถามถึงวันก่อนหน้า (เช่น เมื่อวาน): ตอบตาม "สถิติย้อนหลัง" ที่มีอย่างกระชับ
2. ด้านการใช้ชีวิตประจำวันและสุขภาวะ:
   - หากผู้ใช้ถามถึง "การใช้ชีวิตประจำวัน", "สุขภาพเป็นอย่างไร", "กิจวัตรวันนี้":
     ให้ตอบสรุปสั้นๆ 2-3 บรรทัด โดยระบุคะแนนสุขภาวะ (Activity Score), สภาพแวดล้อมห้องเฉลี่ยทั้งวัน (อุณหภูมิ/ความชื้น) พร้อมสรุปภาพรวมและให้คำแนะนำสั้นๆ 1 ประโยค
3. ด้านสภาพอากาศ/สิ่งแวดล้อม:
   - หากผู้ใช้ถามเรื่องอากาศ อุณหภูมิ ร้อนไหม ในห้องเป็นอย่างไร: ให้นำตัวเลขจริงจากข้อมูลเซนเซอร์สดมาตอบ
4. ด้านอื่นๆ (อาหาร, ออกกำลังกาย): ให้คำแนะนำสั้นๆ มีประโยชน์ เข้าใจง่าย
5. รูปแบบการตอบ: ใช้ภาษาไทยที่สุภาพ อ่านง่าย กระชับ ใช้ emoji ประกอบอย่างพอเหมาะ จัดข้อความเป็น Bullet points ให้อ่านสบายตา"""

        if is_en:
            system_instruction = f"""You are the "Care Assistant" for the Cucumber RS wearable fall-detection system.
You support CAREGIVERS (family members, nurses, care staff) who look after the person wearing the device ("the wearer").
Tone: calm, warm, professional and concise. Never call yourself by any other name.

Live sensor data, real fall statistics and today's daily-living data from the database (may be written in Thai - translate as needed):
{sensor_context}

How to answer:
1. Falls: when asked how many falls happened, when, or how severe - answer from the fall statistics above in 1-2 short lines
   (count, time, impact in G). If there were falls, advise the caregiver to check on the wearer. If none, say today has been safe.
2. Daily wellbeing: summarise the activity score and average room temperature/humidity in 2-3 lines, plus one practical tip.
3. Room environment: use the real live sensor numbers. If the device is offline, say so clearly.
4. Other topics (fall prevention, exercise, nutrition, home safety): short, practical advice a caregiver can act on.
   Do not diagnose; recommend contacting a medical professional when symptoms sound serious.
5. Format: reply in English only, short bullet points, sparing use of emoji."""

        contents = []
        for turn in history[-8:]:
            contents.append({
                "role": turn["role"],
                "parts": [{"text": turn["text"]}]
            })

        contents.append({
            "role": "user",
            "parts": [{"text": query_for_ai}]
        })

        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": contents
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_api_key}"

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    ai_reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()

                    # Save to memory
                    history.append({"role": "user", "text": original_user_text})
                    history.append({"role": "model", "text": ai_reply})
                    if len(history) > 12:
                        self.chat_histories[chat_id] = history[-10:]

                    return ai_reply
                else:
                    if is_en:
                        return f"Sorry, the AI service is temporarily unavailable (code {res.status_code})."
                    return f"ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว (Code: {res.status_code})"
        except Exception as e:
            if is_en:
                return f"Sorry, there was a problem contacting the AI service: {str(e)}"
            return f"ขออภัยครับ เกิดข้อผิดพลาดในการติดต่อ AI: {str(e)}"
