import asyncio
import httpx
import json
from typing import Dict, List, Optional, Callable
from datetime import datetime

# Name the assistant uses everywhere (Telegram bot and the web dashboard chat).
ASSISTANT_NAME = "Care Assistant"
MINI_APP_BUTTON_TEXT = "📱 Open Dashboard"


def translate_risk(risk: Optional[str]) -> str:
    """Return an English risk description.

    Fall events recorded before the switch to English were stored with Thai risk text;
    map those to the English wording so old incidents read correctly too.
    """
    r = str(risk or "")
    if not r:
        return "Fall detected"
    if "ความชื้น" in r:
        return "🚨 Very high risk: unusually high humidity (possible slip on a wet or bathroom floor)"
    if "อุณหภูมิ" in r:
        return "🚨 High risk: unusually high temperature (possible heat stroke or fainting)"
    if "3.5" in r and "แรงกระแทก" in r:
        return "⚠️ High risk: severe impact (> 3.5 G) - check for head injury or fractures"
    if "ปานกลาง" in r:
        return "Moderate risk: fall impact detected"
    return r


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

    def mini_app_keyboard(self, url: Optional[str] = None, text: str = "🚀 Open Caregiver Dashboard") -> Optional[dict]:
        """Inline keyboard with a single button that opens the dashboard as a Telegram Mini App."""
        target = url or self.app_url
        if not target:
            return None
        return {"inline_keyboard": [[{"text": text, "web_app": {"url": target}}]]}

    async def setup_menu_button(self, app_url: str):
        """Configure Telegram persistent bottom menu button to open Mini App."""
        self.app_url = app_url
        if not self.token or not app_url:
            return
        url = f"https://api.telegram.org/bot{self.token}/setChatMenuButton"
        payload = {
            "menu_button": {
                "type": "web_app",
                "text": MINI_APP_BUTTON_TEXT,
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
        user_name = from_user.get("first_name") or "there"

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

    async def ask_gemini(self, chat_id: str, user_text: str) -> str:
        """Unified entry point for Telegram and the web dashboard chat: shortcut commands, then Gemini with live context."""
        cmd = user_text.lower().split()[0] if user_text else ""

        if cmd == "/status":
            return self._build_status_text()

        if cmd in ["/falls", "/fall"]:
            return self._build_falls_report()

        if cmd in ["/summary", "/daily", "/lifestyle"]:
            return self._build_lifestyle_report()

        if cmd == "/clear":
            self.chat_histories[chat_id] = []
            return "🧹 *Conversation cleared.*\nYou can start a new question now."

        # Prepare Sensor Context for Gemini
        sensor_data = self.get_sensor_data()
        emergency_data = self.get_emergency_status()
        sensor_context = self._format_sensor_context(sensor_data, emergency_data)

        # Pre-process built-in command shortcuts
        prompt_override = None
        if cmd == "/weather":
            prompt_override = ("Give the caregiver a short report on the room conditions around the wearer right now, "
                               "using the live sensor data, and say whether it is comfortable and whether the wearer should drink some water.")
        elif cmd == "/food":
            prompt_override = ("Suggest 1-2 healthy, easy-to-digest meal ideas suitable for an older adult, "
                               "with one line on why each is good.")
        elif cmd == "/exercise":
            prompt_override = ("Suggest 2 safe exercises or stretches that improve balance and help prevent falls for an older adult, "
                               "as short step-by-step instructions a caregiver can guide.")

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
        updated = s.get("last_updated") or "waiting for the device..."
        is_em = em.get("active", False)
        em_badge = "🚨 *Fall detected - EMERGENCY ACTIVE*" if is_em else "✅ *Safe*"
        conn_badge = "🟢 *Online*" if is_conn else "🔴 *Offline*"

        return (
            f"📊 *Wearable device status*\n\n"
            f"📡 *Device:* {conn_badge}\n"
            f"🌡️ *Room temperature:* `{temp} °C`\n"
            f"💧 *Humidity:* `{hum} %RH`\n"
            f"🌪️ *Air pressure:* `{press} hPa`\n"
            f"📶 *Wi-Fi signal:* `{rssi} dBm`\n"
            f"⏱️ *Last update:* `{updated}`\n"
            f"🛡️ *Wearer safety:* {em_badge}\n\n"
            f"💡 *Tip:* tap *{MINI_APP_BUTTON_TEXT}* below to see the full dashboard."
        )

    def _build_falls_report(self) -> str:
        """Concise fall incident report directly from SQLite database."""
        f_data = self.get_fall_history()
        today_count = f_data.get("today_count", 0)
        incidents = f_data.get("today_incidents", [])
        yesterday_count = f_data.get("yesterday_count", 0)

        if today_count == 0:
            msg = (
                "🛡️ *Today's fall report*\n\n"
                "✅ *No falls detected today.* The wearer is safe."
            )
            if yesterday_count > 0:
                msg += f"\n_(Yesterday: {yesterday_count} fall{'s' if yesterday_count != 1 else ''})_"
            return msg

        msg = f"🚨 *Today's fall report ({today_count} fall{'s' if today_count != 1 else ''})*\n\n"
        for idx, inc in enumerate(incidents, 1):
            time = inc.get("time", "--:--")
            g = inc.get("max_g", 0.0)
            risk = translate_risk(inc.get("risk"))
            msg += f"• *#{idx}* ({time}): impact `{g} G` | {risk}\n"

        if yesterday_count > 0:
            msg += f"\n_(Yesterday: {yesterday_count} fall{'s' if yesterday_count != 1 else ''})_\n"
        msg += "\n⚠️ *Action:* please check on the wearer and the area around them."
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

        score_badge = "🟢 Good" if score >= 80 else ("🟡 Needs attention" if score >= 50 else "🔴 High risk")

        overview_text = (
            "No falls today and the room conditions are suitable. Keep to the usual routine and encourage regular drinks of water."
            if today_count == 0 else
            f"{today_count} fall{'s' if today_count != 1 else ''} today; the environment may be a risk factor. "
            "Watch the wearer's walking closely and check how they are feeling."
        )

        return (
            f"📋 *Daily wellbeing summary ({date})*\n\n"
            f"🏅 *Activity score:* `{score}/100` [{score_badge}]\n"
            f"🚨 *Falls today:* `{today_count}`\n"
            f"🌡️ *Average room conditions:* temperature `{avg_t} °C` | humidity `{avg_h} %RH`\n\n"
            f"💡 *Overview:* {overview_text}"
        )

    def _format_sensor_context(self, s: dict, em: dict) -> str:
        """Create a compact live sensor, fall incident, and lifestyle context for Gemini AI."""
        is_conn = s.get("is_connected", False)
        temp = s.get("temperature", "--")
        hum = s.get("humidity", "--")
        press = s.get("pressure", "--")
        last_updated = s.get("last_updated")
        em_status = "🚨 Fall detected - waiting for help" if em.get("active") else "✅ Normal, safe"

        if not is_conn or not last_updated:
            sensor_str = ("Live sensors: ⚠️ the Cucumber RS device is OFFLINE (no data for over 15 seconds). "
                          "If asked about the current room conditions, tell the caregiver the device is offline.")
        else:
            sensor_str = (f"Live sensors now: temperature {temp} °C, humidity {hum} %RH, "
                          f"air pressure {press} hPa, last update {last_updated}")

        # Fall incident stats from SQLite
        f_data = self.get_fall_history()
        today_count = f_data.get("today_count", 0)
        incidents = f_data.get("today_incidents", [])
        yesterday_count = f_data.get("yesterday_count", 0)

        if today_count == 0:
            falls_str = "Falls today: none recorded (0 falls, safe)"
        else:
            incident_lines = []
            for idx, inc in enumerate(incidents, 1):
                incident_lines.append(f"  - #{idx} at {inc['time']}: impact {inc['max_g']} G, risk: {translate_risk(inc['risk'])}")
            falls_str = f"Falls today: {today_count} in total:\n" + "\n".join(incident_lines)

        recent_stats_str = f"Yesterday: {yesterday_count} falls"

        # Lifestyle & activity stats from SQLite
        ls = self.get_lifestyle_summary()
        lifestyle_str = (
            f"Today's wellbeing data: activity score = {ls.get('score', 85)}/100, "
            f"average room temperature = {ls.get('avg_temp', '--')} °C, average humidity = {ls.get('avg_hum', '--')} %RH, "
            f"telemetry samples today = {ls.get('total_telemetry_samples', 0)}"
        )

        return (
            f"1. {sensor_str}\n"
            f"   Safety status: {em_status}\n\n"
            f"2. {falls_str}\n"
            f"   {recent_stats_str}\n\n"
            f"3. {lifestyle_str}"
        )

    async def _cmd_help(self, chat_id: str, user_name: str):
        msg = (
            f"👋 *Hello {user_name}! I'm the {ASSISTANT_NAME}*\n"
            f"I help caregivers keep an eye on the person wearing the *Cucumber RS* fall-detection device.\n\n"
            f"📱 *Open the caregiver dashboard*\n"
            f"Tap *{MINI_APP_BUTTON_TEXT}* (next to the message box) or the button below to see live readings, "
            f"movement charts, fall incidents and chat with me - all inside Telegram.\n\n"
            f"💬 *Or just ask me here, for example:*\n"
            f"• 🚨 *\"How many falls were there today, and how serious?\"*\n"
            f"• 📋 *\"How has the wearer's day been?\"*\n"
            f"• 🌡️ *\"Is the room comfortable right now?\"*\n"
            f"• 🏃 *\"Any exercises to help prevent falls?\"*\n\n"
            f"⚡ *Shortcuts:* /status, /falls, /summary, /weather, /food, /exercise, /clear\n\n"
            f"🔔 You will get an alert in this chat as soon as a fall is detected."
        )
        await self.send_reply(chat_id, msg, reply_markup=self.mini_app_keyboard())

    async def _call_gemini_chat(self, chat_id: str, query_for_ai: str, original_user_text: str, sensor_context: str) -> str:
        """Call Google Gemini 2.5 Flash with multi-turn memory and live sensor context."""
        if not self.gemini_api_key:
            return "Sorry, the Google Gemini API key has not been configured."

        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        history = self.chat_histories[chat_id]

        system_instruction = f"""You are the "{ASSISTANT_NAME}" for the Cucumber RS wearable fall-detection system.
You support CAREGIVERS (family members, nurses, care staff) who look after the person wearing the device ("the wearer").
Tone: calm, warm, professional and concise. Never call yourself by any other name.

Live sensor data, real fall statistics and today's daily-living data from the database:
{sensor_context}

How to answer:
1. Falls: when asked how many falls happened, when, or how severe - answer from the fall statistics above in 1-2 short lines
   (count, time, impact in G). If there were falls, advise the caregiver to check on the wearer. If none, say today has been safe.
   If asked about yesterday, answer from the yesterday figure.
2. Daily wellbeing: summarise the activity score and average room temperature/humidity in 2-3 lines, plus one practical tip.
3. Room environment: use the real live sensor numbers. If the device is offline, say so clearly.
4. Other topics (fall prevention, exercise, nutrition, home safety): short, practical advice a caregiver can act on.
   Do not diagnose; recommend contacting a medical professional when symptoms sound serious.
5. Format: reply in English only (even if the question is in another language), short bullet points, sparing use of emoji."""

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
                    return f"Sorry, the AI service is temporarily unavailable (code {res.status_code})."
        except Exception as e:
            return f"Sorry, there was a problem contacting the AI service: {str(e)}"
