import os
import json
import logging
import certifi
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Annotated

# Fix for macOS SSL certificate verification
os.environ["SSL_CERT_FILE"] = certifi.where()

# ── Sentry error tracking (#21) ───────────────────────────────────────────────
import sentry_sdk
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        integrations=[AsyncioIntegration()],
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

load_dotenv()
logger = logging.getLogger("outbound-agent")
logging.basicConfig(level=logging.INFO)

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import openai, sarvam, silero

CONFIG_FILE = "config.json"

# ── Rate limiting (#37) ───────────────────────────────────────────────────────
_call_timestamps: dict = defaultdict(list)
RATE_LIMIT_CALLS  = 5
RATE_LIMIT_WINDOW = 3600  # 1 hour

def is_rate_limited(phone: str) -> bool:
    if phone in ("unknown", "demo"):
        return False
    now = time.time()
    _call_timestamps[phone] = [t for t in _call_timestamps[phone] if now - t < RATE_LIMIT_WINDOW]
    if len(_call_timestamps[phone]) >= RATE_LIMIT_CALLS:
        return True
    _call_timestamps[phone].append(now)
    return False


# ── Config loader (#17 partial — per-client path awareness) ───────────────────
def get_live_config(phone_number: str | None = None):
    """Load config — tries per-client file first, then default config.json."""
    config = {}
    paths = []
    if phone_number and phone_number != "unknown":
        for variant in phone_match_variants(phone_number):
            compact = variant.replace("+", "").replace(" ", "")
            paths.append(f"configs/{compact}.json")
            if compact.startswith("91") and len(compact) == 12:
                paths.append(f"configs/{compact[2:]}.json")
        paths = list(dict.fromkeys(paths))
    paths += ["configs/default.json", CONFIG_FILE]

    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                    logger.info(f"[CONFIG] Loaded: {path}")
                    break
            except Exception as e:
                logger.error(f"[CONFIG] Failed to read {path}: {e}")

    return {
        "agent_instructions":       config.get("agent_instructions", ""),
        "stt_min_endpointing_delay":config.get("stt_min_endpointing_delay", 0.05),
        "llm_model":                config.get("llm_model", "gpt-4o-mini"),
        "llm_provider":             config.get("llm_provider", "openai"),
        "tts_voice":                config.get("tts_voice", "kavya"),
        "tts_language":             config.get("tts_language", "hi-IN"),
        "tts_provider":             config.get("tts_provider", "sarvam"),
        "stt_provider":             config.get("stt_provider", "sarvam"),
        "stt_language":             config.get("stt_language", "unknown"),
        "lang_preset":              config.get("lang_preset", "multilingual"),
        "max_turns":                config.get("max_turns", 25),
        **config,
    }


# ── Token counter (#11) ───────────────────────────────────────────────────────
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


def is_destination_busy_error(error_message: str) -> bool:
    """Return True only for explicit SIP busy signals from the destination."""
    msg = (error_message or "").lower()

    explicit_busy_markers = (
        "sip 486",
        "486 busy",
        "busy here",
        "user busy",
        "callee busy",
        "sip 600",
        "600 busy",
        "busy everywhere",
    )
    if any(marker in msg for marker in explicit_busy_markers):
        return True

    # Avoid misclassifying generic "in use"/"busy" infrastructure errors
    # (e.g., room/participant identity conflicts) as destination busy.
    if "busy" in msg:
        has_sip_context = any(k in msg for k in ("sip", "pstn", "invite", "response code", "status code"))
        has_identity_conflict = any(
            k in msg for k in ("participant_identity", "already exists", "already in use", "identity", "room")
        )
        return has_sip_context and not has_identity_conflict

    return False


# ── IST time context ──────────────────────────────────────────────────────────
def get_ist_time_context() -> str:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str  = now.strftime("%I:%M %p")
    days_lines = []
    for i in range(7):
        day   = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}")
    days_block = "\n".join(days_lines)
    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Resolve ALL relative day references using this table:\n{days_block}\n"
        f"Always use ISO dates when calling save_booking_intent. Appointments in IST (+05:30).]"
    )


# ── Language presets ──────────────────────────────────────────────────────────
LANGUAGE_PRESETS = {
    "hinglish":    {"label": "Hinglish (Hindi+English)", "tts_language": "hi-IN", "tts_voice": "kavya",  "instruction": "Speak in natural Hinglish — mix Hindi and English like educated Indians do. Default to Hindi but use English words when more natural."},
    "hindi":       {"label": "Hindi",                   "tts_language": "hi-IN", "tts_voice": "ritu",   "instruction": "Speak only in pure Hindi. Avoid English words wherever a Hindi equivalent exists."},
    "english":     {"label": "English (India)",         "tts_language": "en-IN", "tts_voice": "dev",    "instruction": "Speak only in Indian English with a warm, professional tone."},
    "tamil":       {"label": "Tamil",                   "tts_language": "ta-IN", "tts_voice": "priya",  "instruction": "Speak only in Tamil. Use standard spoken Tamil for a professional context."},
    "telugu":      {"label": "Telugu",                  "tts_language": "te-IN", "tts_voice": "kavya",  "instruction": "Speak only in Telugu. Use clear, polite spoken Telugu."},
    "gujarati":    {"label": "Gujarati",                "tts_language": "gu-IN", "tts_voice": "rohan",  "instruction": "Speak only in Gujarati. Use polite, professional Gujarati."},
    "bengali":     {"label": "Bengali",                 "tts_language": "bn-IN", "tts_voice": "neha",   "instruction": "Speak only in Bengali (Bangla). Use standard, polite spoken Bengali."},
    "marathi":     {"label": "Marathi",                 "tts_language": "mr-IN", "tts_voice": "shubh",  "instruction": "Speak only in Marathi. Use polite, standard spoken Marathi."},
    "kannada":     {"label": "Kannada",                 "tts_language": "kn-IN", "tts_voice": "rahul",  "instruction": "Speak only in Kannada. Use clear, professional spoken Kannada."},
    "malayalam":   {"label": "Malayalam",               "tts_language": "ml-IN", "tts_voice": "ritu",   "instruction": "Speak only in Malayalam. Use polite, professional spoken Malayalam."},
    "multilingual":{"label": "Multilingual (Auto)",     "tts_language": "hi-IN", "tts_voice": "kavya",  "instruction": "Detect the caller's language from their first message and reply in that SAME language for the entire call. Supported: Hindi, Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, Kannada, Malayalam. Switch if caller switches."},
}

def get_language_instruction(lang_preset: str) -> str:
    preset = LANGUAGE_PRESETS.get(lang_preset, LANGUAGE_PRESETS["multilingual"])
    return f"\n\n[LANGUAGE DIRECTIVE]\n{preset['instruction']}"


_TTS_SCRIPT_PATTERNS = {
    "hi-IN": r"[\u0900-\u097F]",  # Devanagari
    "mr-IN": r"[\u0900-\u097F]",  # Devanagari
    "bn-IN": r"[\u0980-\u09FF]",  # Bengali
    "gu-IN": r"[\u0A80-\u0AFF]",  # Gujarati
    "ta-IN": r"[\u0B80-\u0BFF]",  # Tamil
    "te-IN": r"[\u0C00-\u0C7F]",  # Telugu
    "kn-IN": r"[\u0C80-\u0CFF]",  # Kannada
    "ml-IN": r"[\u0D00-\u0D7F]",  # Malayalam
}

_TTS_SAFE_PREFIX = {
    "hi-IN": "नमस्ते",
    "mr-IN": "नमस्कार",
    "bn-IN": "নমস্কার",
    "gu-IN": "નમસ્તે",
    "ta-IN": "வணக்கம்",
    "te-IN": "నమస్కారం",
    "kn-IN": "ನಮಸ್ಕಾರ",
    "ml-IN": "നമസ്കാരം",
}


def enforce_tts_language_marker(text: str, tts_language: str, tts_provider: str = "sarvam") -> str:
    cleaned = (text or "").replace("—", "-").strip()
    if not cleaned:
        cleaned = "Hello."
    if tts_provider != "sarvam":
        return cleaned

    pattern = _TTS_SCRIPT_PATTERNS.get(tts_language)
    if not pattern:
        return cleaned
    if re.search(pattern, cleaned):
        return cleaned

    prefix = _TTS_SAFE_PREFIX.get(tts_language, "")
    return f"{prefix}. {cleaned}" if prefix else cleaned


def infer_call_insights(
    transcript_text: str,
    booking_status_msg: str,
    booking_detected: bool,
    sentiment: str,
) -> tuple[str, str]:
    tl = (transcript_text or "").lower()
    booking_text = (booking_status_msg or "").lower()

    if booking_detected or "booking confirmed" in booking_text or "appointment" in tl:
        purpose = "Appointment booking"
    elif "cancel" in tl or "resched" in tl:
        purpose = "Appointment update"
    elif any(k in tl for k in ("price", "cost", "fee", "charges", "timing", "hours", "open", "location", "address")):
        purpose = "General inquiry"
    elif any(k in tl for k in ("problem", "issue", "pain", "urgent", "emergency", "help")):
        purpose = "Support request"
    else:
        purpose = "General conversation"

    if transcript_text and transcript_text != "unavailable":
        first_user = ""
        for line in transcript_text.splitlines():
            if line.startswith("[USER] "):
                first_user = line[len("[USER] "):].strip()
                break
        if first_user:
            first_user = first_user[:220]
            call_summary = f"Caller intent: {first_user}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."
        else:
            call_summary = f"Call completed. Purpose: {purpose}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."
    else:
        call_summary = f"Call completed. Purpose: {purpose}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."

    return purpose, call_summary


# ── External imports ──────────────────────────────────────────────────────────
from calendar_tools import get_available_slots, create_booking, cancel_booking
import db
from notify import (
    notify_booking_confirmed,
    notify_call_no_booking,
)
from phone_utils import normalize_phone_number, is_valid_phone_number, phone_match_variants


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CONTEXT — All AI-callable functions
# ══════════════════════════════════════════════════════════════════════════════

class AgentTools(llm.ToolContext):

    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent: dict | None = None
        self.sip_domain          = os.getenv("VOBIZ_SIP_DOMAIN")
        self.ctx_api             = None
        self.room_name           = None
        self._sip_identity       = None

    # ── Tool: Transfer to Human ───────────────────────────────────────────
    @llm.function_tool(description="Transfer this call to a human agent. Use if: caller asks for human, is angry, or query is outside scope.")
    async def transfer_call(
        self,
        context: Annotated[str, "Optional context note; can be left empty."] = "",
    ) -> str:
        logger.info("[TOOL] transfer_call triggered")
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
        if destination and self.sip_domain and "@" not in destination:
            clean_dest  = destination.replace("tel:", "").replace("sip:", "")
            destination = f"sip:{clean_dest}@{self.sip_domain}"
        if destination and not destination.startswith("sip:"):
            destination = f"sip:{destination}"
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False,
                    )
                )
                return "Transfer initiated successfully."
            return "Unable to transfer right now."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return "Unable to transfer right now."

    # ── Tool: End Call ────────────────────────────────────────────────────
    @llm.function_tool(description="End the call. Use ONLY when caller says bye/goodbye or after booking is fully confirmed.")
    async def end_call(
        self,
        reason: Annotated[str, "Optional reason for ending the call; can be left empty."] = "",
    ) -> str:
        logger.info("[TOOL] end_call triggered — hanging up.")
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+00000000",
                        play_dialtone=False,
                    )
                )
        except Exception as e:
            logger.warning(f"[END-CALL] SIP hangup failed: {e}")
        return "Call ended."

    # ── Tool: Save Booking Intent ─────────────────────────────────────────
    @llm.function_tool(
        description=(
            "Save booking intent after caller confirms appointment. "
            "Call this ONCE only after you have caller name, caller phone, and confirmed date+time. "
            "Use only the defined arguments (start_time, caller_name, caller_phone, notes)."
        )
    )
    async def save_booking_intent(
        self,
        start_time:  Annotated[str,  "ISO 8601 datetime e.g. '2026-03-01T10:00:00+05:30'"],
        caller_name: Annotated[str,  "Full name of the caller"],
        caller_phone:Annotated[str,  "Phone number of the caller"],
        notes:       Annotated[str,  "Any notes, email, or special requests"] = "",
    ) -> str:
        logger.info(f"[TOOL] save_booking_intent: {caller_name} at {start_time}")
        try:
            self.booking_intent = {
                "start_time":   start_time,
                "caller_name":  caller_name,
                "caller_phone": caller_phone,
                "notes":        notes,
            }
            self.caller_name = caller_name
            return f"Booking intent saved for {caller_name} at {start_time}. I'll confirm after the call."
        except Exception as e:
            logger.error(f"[TOOL] save_booking_intent failed: {e}")
            return "I had trouble saving the booking. Please try again."

    # ── Tool: Check Availability (#13) ────────────────────────────────────
    @llm.function_tool(description="Check available appointment slots for a given date. Call this when user asks about availability.")
    async def check_availability(
        self,
        date: Annotated[str, "Date to check in YYYY-MM-DD format e.g. '2026-03-01'"],
    ) -> str:
        logger.info(f"[TOOL] check_availability: date={date}")
        try:
            slots = await get_available_slots(date)
            if not slots:
                return f"No available slots on {date}. Would you like to check another date?"
            slot_strings = [s.get("start_time", str(s))[-8:][:5] for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)} IST."
        except Exception as e:
            logger.error(f"[TOOL] check_availability failed: {e}")
            return "I'm having trouble checking the calendar right now."

    # ── Tool: Business Hours (#31) ────────────────────────────────────────
    @llm.function_tool(description="Check if the business is currently open and what the operating hours are.")
    async def get_business_hours(
        self,
        context: Annotated[str, "Optional context note; can be left empty."] = "",
    ) -> str:
        ist  = pytz.timezone("Asia/Kolkata")
        now  = datetime.now(ist)
        hours = {
            0: ("Monday",    "10:00", "19:00"),
            1: ("Tuesday",   "10:00", "19:00"),
            2: ("Wednesday", "10:00", "19:00"),
            3: ("Thursday",  "10:00", "19:00"),
            4: ("Friday",    "10:00", "19:00"),
            5: ("Saturday",  "10:00", "17:00"),
            6: ("Sunday",    None,    None),
        }
        day_name, open_t, close_t = hours[now.weekday()]
        current_time = now.strftime("%H:%M")
        if open_t is None:
            return "We are closed on Sundays. Next opening: Monday 10:00 AM IST."
        if open_t <= current_time <= close_t:
            return f"We are OPEN. Today ({day_name}): {open_t}–{close_t} IST."
        return f"We are CLOSED. Today ({day_name}): {open_t}–{close_t} IST."


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class OutboundAssistant(Agent):

    def __init__(self, agent_tools: AgentTools, first_line: str = "", live_config: dict | None = None, fallback_message: str = ""):
        tools = llm.find_function_tools(agent_tools)
        self._first_line  = first_line
        self._live_config = live_config or {}
        self._agent_tools = agent_tools
        self._fallback_message = fallback_message.strip()
        live_config_loaded = self._live_config

        base_instructions = live_config_loaded.get("agent_instructions", "")
        ist_context       = get_ist_time_context()
        lang_preset       = live_config_loaded.get("lang_preset", "multilingual")
        lang_instruction  = get_language_instruction(lang_preset)
        tts_language      = live_config_loaded.get("tts_language", "hi-IN")
        tts_provider      = live_config_loaded.get("tts_provider", "sarvam")
        tts_safety_note   = ""
        if tts_provider == "sarvam" and tts_language in _TTS_SCRIPT_PATTERNS:
            safe_token = _TTS_SAFE_PREFIX.get(tts_language, "नमस्ते")
            tts_safety_note = (
                "\n\n[TTS SAFETY]\n"
                "Always include at least one native-script word in every reply for voice synthesis. "
                f"You can prepend '{safe_token}' naturally when needed."
            )
        tool_safety_note = (
            "\n\n[TOOL CALL SAFETY]\n"
            "Call tools only when required and only with valid arguments. "
            "Never invent missing required fields. "
            "If any required detail is missing, ask the caller a short follow-up question instead of calling a tool."
        )
        final_instructions = base_instructions + ist_context + lang_instruction + tts_safety_note + tool_safety_note

        # Token counter (#11)
        token_count = count_tokens(final_instructions)
        logger.info(f"[PROMPT] System prompt: {token_count} tokens")
        if token_count > 600:
            logger.warning(f"[PROMPT] Prompt exceeds 600 tokens — consider trimming for latency")

        super().__init__(instructions=final_instructions, tools=tools)

    async def on_enter(self):
        if self._fallback_message:
            speech = self.session.say(self._fallback_message, add_to_chat_ctx=False)
            await speech.wait_for_playout()
            await self._agent_tools.end_call()
            return

        greeting = self._live_config.get(
            "first_line",
            self._first_line or (
                "Namaste! This is Aakriti Clinic — How can I help you?."
            )
        )
        greeting = enforce_tts_language_marker(
            text=greeting,
            tts_language=self._live_config.get("tts_language", "hi-IN"),
            tts_provider=self._live_config.get("tts_provider", "sarvam"),
        )
        # Avoid first-turn LLM tool-calling failures by speaking greeting directly.
        speech = self.session.say(greeting, add_to_chat_ctx=True)
        await speech.wait_for_playout()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

agent_is_speaking = False

async def entrypoint(ctx: JobContext):
    global agent_is_speaking
    shutdown_done = False
    shutdown_lock = asyncio.Lock()
    active_calls_enabled = True
    call_transcripts_enabled = True

    # ── Connect ───────────────────────────────────────────────────────────
    await ctx.connect()
    logger.info(f"[ROOM] Connected: {ctx.room.name}")

    # ── Extract caller info ───────────────────────────────────────────────
    dispatch_phone_number = None
    participant_phone_number = None
    caller_name  = ""
    caller_phone = "unknown"

    # Try metadata first (outbound dispatch)
    metadata = ctx.job.metadata or ""
    if metadata:
        try:
            meta = json.loads(metadata)
            dispatch_phone_number = meta.get("phone_number")
        except Exception:
            pass

    # Extract from SIP participants
    for identity, participant in ctx.room.remote_participants.items():
        # Name from caller ID (#32)
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
            logger.info(f"[CALLER-ID] Name from SIP: {caller_name}")
        if not participant_phone_number:
            attr = participant.attributes or {}
            participant_phone_number = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if not participant_phone_number and "+" in identity:
            import re as _re
            m = _re.search(r"\+\d{7,15}", identity)
            if m:
                participant_phone_number = m.group()

    caller_phone = normalize_phone_number(dispatch_phone_number or participant_phone_number or "unknown")
    is_outbound_job = bool(dispatch_phone_number and dispatch_phone_number != "demo")

    # ── Rate limiting (#37) ───────────────────────────────────────────────
    if is_rate_limited(caller_phone):
        logger.warning(f"[RATE-LIMIT] Blocked {caller_phone} — too many calls in 1h")
        return

    # ── Load config ───────────────────────────────────────────────────────
    live_config   = get_live_config(caller_phone)
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.05)
    llm_model     = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider  = live_config.get("llm_provider", "openai")
    tts_voice     = live_config.get("tts_voice", "kavya")
    tts_language  = live_config.get("tts_language", "hi-IN")
    tts_provider  = live_config.get("tts_provider", "sarvam")
    stt_provider  = live_config.get("stt_provider", "sarvam")
    stt_language  = live_config.get("stt_language", "unknown")  # auto-detect (#20)
    max_turns     = live_config.get("max_turns", 25)

    # Override OS env vars from UI config
    for key in ["LIVEKIT_URL","LIVEKIT_API_KEY","LIVEKIT_API_SECRET","OPENAI_API_KEY",
                "GROQ_API_KEY","ANTHROPIC_API_KEY","SARVAM_API_KEY","CAL_API_KEY","SUPABASE_URL","SUPABASE_KEY"]:
        val = live_config.get(key.lower(), "")
        if val:
            os.environ[key] = val

    # ── Outbound SIP dial (explicit) ──────────────────────────────────────
    if is_outbound_job:
        sip_trunk_id = (
            live_config.get("sip_trunk_id")
            or os.getenv("SIP_TRUNK_ID")
            or os.getenv("OUTBOUND_TRUNK_ID")
        )
        dial_to = normalize_phone_number(caller_phone)

        if not sip_trunk_id:
            logger.error("[DIAL] Missing SIP trunk id. Set sip_trunk_id in config.json or SIP_TRUNK_ID/OUTBOUND_TRUNK_ID in .env")
            return
        if not is_valid_phone_number(dial_to):
            logger.error(
                f"[DIAL] Invalid destination number format: {caller_phone}. "
                "For India use +91XXXXXXXXXX, 91XXXXXXXXXX, or XXXXXXXXXX (10 digits)."
            )
            return

        sip_identity = f"sip_{dial_to.replace('+', '')}"
        dial_ok = False
        for attempt in range(1, 3):
            try:
                logger.info(f"[DIAL] Outbound via trunk={sip_trunk_id} to={dial_to} (attempt {attempt}/2)")
                await ctx.api.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=sip_trunk_id,
                        sip_call_to=dial_to,
                        room_name=ctx.room.name,
                        participant_identity=sip_identity,
                        participant_name=dial_to,
                        play_dialtone=False,
                        wait_until_answered=False,
                    )
                )
                caller_phone = dial_to
                dial_ok = True
                logger.info("[DIAL] SIP INVITE submitted successfully")
                break
            except Exception as e:
                msg = str(e)
                busy_like = is_destination_busy_error(msg)
                if busy_like and attempt < 2:
                    logger.warning("[DIAL] Destination returned SIP busy (486/600). Retrying once in 2s...")
                    await asyncio.sleep(2)
                    continue
                logger.error(f"[DIAL] SIP call creation failed: {e}")
                logger.error("[DIAL] Common causes: active PSTN leg not yet released by carrier, country route disabled, insufficient SIP credits, or trunk auth mismatch")
                return

        if not dial_ok:
            return

    # ── Caller memory (#15) ───────────────────────────────────────────────
    async def get_caller_history(phone: str) -> str:
        if phone == "unknown":
            return ""
        try:
            sb = db.get_supabase()
            if not sb:
                return ""
            variants = phone_match_variants(phone)
            query = (
                sb.table("call_logs")
                .select("summary, created_at")
                .order("created_at", desc=True)
                .limit(1)
            )
            if variants:
                query = query.in_("phone_number", variants)
            else:
                query = query.eq("phone_number", phone)
            result = query.execute()
            if result.data:
                last = result.data[0]
                return f"\n\n[CALLER HISTORY: Last call {last['created_at'][:10]}. Summary: {last['summary']}]"
        except Exception as e:
            logger.warning(f"[MEMORY] Could not load history: {e}")
        return ""

    caller_history = await get_caller_history(caller_phone)
    if caller_history:
        logger.info(f"[MEMORY] Loaded caller history for {caller_phone}")
        # Append to live_config instructions
        live_config["agent_instructions"] = (live_config.get("agent_instructions","") + caller_history)

    # ── Instantiate tools ─────────────────────────────────────────────────
    agent_tools = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    agent_tools._sip_identity = (
        f"sip_{caller_phone.replace('+','')}" if caller_phone != "unknown" else "inbound_caller"
    )
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name

    # ── Build LLM (#8 Groq support + connection-error failover) ──────────
    fallback_message = ""
    groq_model = live_config.get("groq_model", "llama-3.3-70b-versatile")
    claude_model = live_config.get("claude_model", "claude-haiku-3-5-latest")

    def _switch_to_fallback(reason_text: str):
        nonlocal llm_provider, llm_model, fallback_message
        if os.environ.get("GROQ_API_KEY"):
            logger.warning(
                f"[LLM] OpenAI unavailable ({reason_text}). "
                f"Auto-falling back to Groq model={groq_model}."
            )
            llm_provider = "groq"
            llm_model = groq_model
            return
        if os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                f"[LLM] OpenAI unavailable ({reason_text}). "
                f"Auto-falling back to Claude model={claude_model}."
            )
            llm_provider = "claude"
            llm_model = claude_model
            return
        logger.error(
            f"[LLM] OpenAI unavailable ({reason_text}) and no fallback key is configured. "
            "Using a no-LLM fallback response for this call."
        )
        llm_provider = "none"
        fallback_message = (
            "Hmm, I’m having a temporary technical issue right now. "
            "Please try again in a few minutes."
        )

    if llm_provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            _switch_to_fallback("missing OPENAI_API_KEY")
        else:
            try:
                import openai as _oai
                _probe_client = _oai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
                await _probe_client.chat.completions.create(
                    model=llm_model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "ping"}],
                )
            except Exception as e:
                err = str(e).lower()
                dns_or_connect = any(k in err for k in (
                    "apiconnectionerror",
                    "connection error",
                    "connecterror",
                    "getaddrinfo failed",
                    "name or service not known",
                    "temporary failure in name resolution",
                    "nodename nor servname provided",
                    "timeout",
                ))
                if "insufficient_quota" in err:
                    _switch_to_fallback("insufficient_quota")
                elif dns_or_connect:
                    _switch_to_fallback("connection/dns failure")
                else:
                    logger.warning(f"[LLM] OpenAI probe failed but continuing with OpenAI: {e}")
    if llm_provider == "groq":
        # Groq uses OpenAI-compatible API
        _groq_key = os.environ.get("GROQ_API_KEY", "")
        if not _groq_key:
            logger.warning("[LLM] GROQ_API_KEY missing; falling back to no-LLM mode.")
            llm_provider = "none"
            fallback_message = (
                "Hmm, I’m having a temporary technical issue right now. "
                "Please try again in a few minutes."
            )
            agent_llm = None
        else:
            agent_llm = openai.LLM(
                model=llm_model or "llama-3.3-70b-versatile",
                base_url="https://api.groq.com/openai/v1",
                api_key=_groq_key,
                max_completion_tokens=120,
            )
            logger.info(f"[LLM] Using Groq: {llm_model}")
    elif llm_provider == "claude":
        # Claude Haiku 3.5 via Anthropic API (#27)
        _anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not _anthropic_key:
            logger.warning("[LLM] ANTHROPIC_API_KEY missing; falling back to no-LLM mode.")
            llm_provider = "none"
            fallback_message = (
                "Hmm, I’m having a temporary technical issue right now. "
                "Please try again in a few minutes."
            )
            agent_llm = None
        else:
            agent_llm = openai.LLM(
                model=llm_model or "claude-haiku-3-5-latest",
                base_url="https://api.anthropic.com/v1/",
                api_key=_anthropic_key,
                max_completion_tokens=120,
            )
            logger.info(f"[LLM] Using Claude via Anthropic: {llm_model}")
    elif llm_provider == "none":
        agent_llm = None
        logger.info("[LLM] Using no-LLM fallback mode")
    else:
        agent_llm = openai.LLM(model=llm_model, max_completion_tokens=120)  # cap tokens (#7)
        logger.info(f"[LLM] Using OpenAI: {llm_model}")

    # ── Build STT (#1 16kHz, #20 auto-detect, #9 Deepgram) ──────────────
    if stt_provider == "deepgram":
        try:
            from livekit.plugins import deepgram
            agent_stt = deepgram.STT(
                model="nova-2-general",
                language="multi",        # multilingual mode
                interim_results=False,
            )
            logger.info("[STT] Using Deepgram Nova-2")
        except ImportError:
            logger.warning("[STT] deepgram plugin not installed — falling back to Sarvam")
            agent_stt = sarvam.STT(
                language=stt_language,
                model="saaras:v3",
                mode="translate",
                flush_signal=True,
                sample_rate=16000,
            )
    else:
        agent_stt = sarvam.STT(
            language=stt_language,      # "unknown" = auto-detect (#20)
            model="saaras:v3",
            mode="translate",
            flush_signal=True,
            sample_rate=16000,          # force 16kHz (#1)
        )
        logger.info("[STT] Using Sarvam Saaras v3")

    # ── Build TTS (#2 24kHz, #10 ElevenLabs) ────────────────────────────
    if tts_provider == "elevenlabs":
        try:
            from livekit.plugins import elevenlabs
            _el_voice_id = live_config.get("elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM")
            agent_tts = elevenlabs.TTS(
                model="eleven_turbo_v2_5",
                voice_id=_el_voice_id,
            )
            logger.info(f"[TTS] Using ElevenLabs Turbo v2.5 — voice: {_el_voice_id}")
        except ImportError:
            logger.warning("[TTS] elevenlabs plugin not installed — falling back to Sarvam")
            agent_tts = sarvam.TTS(
                target_language_code=tts_language,
                model="bulbul:v3",
                speaker=tts_voice,
                speech_sample_rate=24000,
            )
    else:
        agent_tts = sarvam.TTS(
            target_language_code=tts_language,
            model="bulbul:v3",
            speaker=tts_voice,
            speech_sample_rate=24000,          # force 24kHz (#2)
        )
        logger.info(f"[TTS] Using Sarvam Bulbul v3 — voice: {tts_voice} lang: {tts_language}")

    # ── Sentence chunker (keep responses short for voice) ─────────────────
    def before_tts_cb(agent_response: str) -> str:
        sentences = re.split(r'(?<=[।.!?])\s+', agent_response.strip())
        return sentences[0] if sentences else agent_response

    # ── Turn counter + auto-close (#29) ──────────────────────────────────
    turn_count    = 0
    interrupt_count = 0  # (#30)

    # ── Build agent ───────────────────────────────────────────────────────
    agent = OutboundAssistant(
        agent_tools=agent_tools,
        first_line=live_config.get("first_line", ""),
        live_config=live_config,
        fallback_message=fallback_message,
    )

    # ── Build session (#3 noise cancellation attempted) ───────────────────
    try:
        from livekit.agents import noise_cancellation as nc
        _noise_cancel = nc.BVC()
        logger.info("[AUDIO] BVC noise cancellation enabled")
    except Exception:
        _noise_cancel = None
        logger.info("[AUDIO] BVC not available — running without noise cancellation")

    room_input = RoomInputOptions(close_on_disconnect=False)
    if _noise_cancel:
        try:
            room_input = RoomInputOptions(close_on_disconnect=False, noise_cancellation=_noise_cancel)
        except Exception:
            room_input = RoomInputOptions(close_on_disconnect=False)

    session = AgentSession(
        stt=agent_stt,
        llm=agent_llm,
        tts=agent_tts,
        turn_detection="stt",
        min_endpointing_delay=float(delay_setting),  # 0.05 default (#6)
        allow_interruptions=True,
    )

    await session.start(room=ctx.room, agent=agent, room_input_options=room_input)

    # ── TTS pre-warm (#12) ────────────────────────────────────────────────
    try:
        prewarm_fn = getattr(session.tts, "prewarm", None)
        if callable(prewarm_fn):
            maybe_coro = prewarm_fn()
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
            logger.info("[TTS] Pre-warmed successfully")
        else:
            logger.debug("[TTS] Pre-warm not supported by current TTS plugin")
    except Exception as e:
        logger.debug(f"[TTS] Pre-warm skipped: {e}")

    logger.info("[AGENT] Session live — waiting for caller audio.")
    call_start_time = datetime.now()

    # ── Recording → Supabase Storage ─────────────────────────────────────
    egress_id = None
    try:
        rec_api = api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=f"recordings/{ctx.room.name}.ogg",
                    s3=api.S3Upload(
                        access_key=os.environ["SUPABASE_S3_ACCESS_KEY"],
                        secret=os.environ["SUPABASE_S3_SECRET_KEY"],
                        bucket="call-recordings",
                        region=os.environ.get("SUPABASE_S3_REGION", "ap-south-1"),
                        endpoint=os.environ["SUPABASE_S3_ENDPOINT"],
                        force_path_style=True,
                    )
                )]
            )
        )
        egress_id = egress_resp.egress_id
        await rec_api.aclose()
        logger.info(f"[RECORDING] Started egress: {egress_id}")
    except Exception as e:
        logger.warning(f"[RECORDING] Failed to start recording: {e}")

    # ── Upsert active_calls (#38) ─────────────────────────────────────────
    async def upsert_active_call(status: str):
        nonlocal active_calls_enabled
        if not active_calls_enabled:
            return
        try:
            sb = db.get_supabase()
            if sb:
                sb.table("active_calls").upsert({
                    "room_id":     ctx.room.name,
                    "phone":       caller_phone,
                    "caller_name": caller_name,
                    "status":      status,
                    "last_updated": datetime.utcnow().isoformat(),
                }).execute()
        except Exception as e:
            err = str(e)
            if "PGRST205" in err and "active_calls" in err.lower():
                active_calls_enabled = False
                logger.info("[ACTIVE-CALL] Table not found; skipping active_calls updates.")
            else:
                logger.debug(f"[ACTIVE-CALL] {e}")

    await upsert_active_call("active")

    # ── Real-time transcript streaming (#33) ─────────────────────────────
    async def _log_transcript(role: str, content: str):
        nonlocal call_transcripts_enabled
        if not call_transcripts_enabled:
            return
        try:
            sb = db.get_supabase()
            if sb:
                sb.table("call_transcripts").insert({
                    "call_room_id": ctx.room.name,
                    "phone":        normalize_phone_number(caller_phone),
                    "role":         role,
                    "content":      content,
                }).execute()
        except Exception as e:
            err = str(e)
            if "PGRST205" in err and "call_transcripts" in err.lower():
                call_transcripts_enabled = False
                logger.info("[TRANSCRIPT-STREAM] Table not found; skipping transcript streaming.")
            else:
                logger.debug(f"[TRANSCRIPT-STREAM] {e}")

    # ── Session event handlers ────────────────────────────────────────────
    @session.on("agent_speech_started")
    def _agent_speech_started(ev):
        global agent_is_speaking
        agent_is_speaking = True

    @session.on("agent_speech_finished")
    def _agent_speech_finished(ev):
        global agent_is_speaking
        agent_is_speaking = False

    # Interrupt logging (#30)
    @session.on("agent_speech_interrupted")
    def _on_interrupted(ev):
        nonlocal interrupt_count
        interrupt_count += 1
        logger.info(f"[INTERRUPT] Agent interrupted. Total: {interrupt_count}")

    FILLER_WORDS = {
        "okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes",
        "no", "um", "ah", "oh", "right", "sure", "fine", "good",
        "haan", "han", "theek", "theek hai", "accha", "ji", "ha",
    }

    @session.on("user_speech_committed")
    def on_user_speech_committed(ev):
        nonlocal turn_count
        global agent_is_speaking

        transcript = ev.user_transcript.strip()
        transcript_lower = transcript.lower().rstrip(".")

        if agent_is_speaking:
            logger.debug(f"[FILTER-ECHO] Dropped: '{transcript}'")
            return
        if not transcript or len(transcript) < 3:
            return
        if transcript_lower in FILLER_WORDS:
            logger.debug(f"[FILTER-FILLER] Dropped: '{transcript}'")
            return

        # Real-time transcript stream
        asyncio.create_task(_log_transcript("user", transcript))

        # Turn counter + auto-close (#29)
        turn_count += 1
        logger.info(f"[TRANSCRIPT] Turn {turn_count}/{max_turns}: '{transcript}'")
        if turn_count >= max_turns:
            logger.info(f"[LIMIT] Reached {max_turns} turns — wrapping up")
            asyncio.create_task(
                session.generate_reply(
                    instructions="Politely wrap up: thank the caller, say they can call back anytime, and say a warm goodbye."
                )
            )

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        global agent_is_speaking
        logger.info(f"[HANGUP] Participant disconnected: {participant.identity}")
        agent_is_speaking = False
        asyncio.create_task(unified_shutdown_hook(ctx))

    # ══════════════════════════════════════════════════════════════════════
    # POST-CALL SHUTDOWN HOOK
    # ══════════════════════════════════════════════════════════════════════

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        nonlocal shutdown_done
        async with shutdown_lock:
            if shutdown_done:
                logger.info("[SHUTDOWN] Already processed — skipping duplicate trigger.")
                return
            shutdown_done = True

        logger.info("[SHUTDOWN] Sequence started.")

        duration = int((datetime.now() - call_start_time).total_seconds())

        # Booking
        booking_status_msg = "No booking"
        if agent_tools.booking_intent:
            from calendar_tools import async_create_booking
            intent = agent_tools.booking_intent
            result = await async_create_booking(
                start_time=intent["start_time"],
                caller_name=intent["caller_name"] or "Unknown Caller",
                caller_phone=intent["caller_phone"],
                notes=intent["notes"],
            )
            if result.get("success"):
                notify_booking_confirmed(
                    caller_name=intent["caller_name"],
                    caller_phone=intent["caller_phone"],
                    booking_time_iso=intent["start_time"],
                    booking_id=result.get("booking_id"),
                    notes=intent["notes"],
                    tts_voice=tts_voice,
                    ai_summary="",
                )
                booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
            else:
                booking_status_msg = f"Booking Failed: {result.get('message')}"
        else:
            notify_call_no_booking(
                caller_name=agent_tools.caller_name,
                caller_phone=agent_tools.caller_phone,
                call_summary="Caller did not schedule during this call.",
                tts_voice=tts_voice,
                duration_seconds=duration,
            )

        # Build transcript
        transcript_text = ""
        try:
            messages = agent.chat_ctx.messages
            if callable(messages):
                messages = messages()
            lines = []
            for msg in messages:
                if getattr(msg, "role", None) in ("user", "assistant"):
                    content = getattr(msg, "content", "")
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content if isinstance(c, str))
                    lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(lines)
        except Exception as e:
            logger.error(f"[SHUTDOWN] Transcript read failed: {e}")
            transcript_text = "unavailable"

        # Sentiment analysis (#14)
        sentiment = "unknown"
        if transcript_text and transcript_text != "unavailable":
            try:
                transcript_lower = transcript_text.lower()
                positive_markers = ("thanks", "thank you", "great", "good", "yes", "confirmed", "booked")
                negative_markers = ("not interested", "bad", "no", "problem", "issue", "angry", "frustrated")
                frustrated_markers = ("frustrated", "upset", "angry", "irritated", "annoyed")
                if any(marker in transcript_lower for marker in frustrated_markers):
                    sentiment = "frustrated"
                elif any(marker in transcript_lower for marker in negative_markers):
                    sentiment = "negative"
                elif any(marker in transcript_lower for marker in positive_markers):
                    sentiment = "positive"
                else:
                    sentiment = "neutral"
                logger.info(f"[SENTIMENT] {sentiment}")
            except Exception as e:
                logger.warning(f"[SENTIMENT] Failed: {e}")

        # Cost estimation (#34)
        def estimate_cost(dur: int, chars: int) -> float:
            return round(
                (dur / 60) * 0.002 +
                (dur / 60) * 0.006 +
                (chars / 1000) * 0.003 +
                (chars / 4000) * 0.0001,
                5
            )
        estimated_cost = estimate_cost(duration, len(transcript_text))
        logger.info(f"[COST] Estimated: ${estimated_cost}")

        call_purpose, call_summary = infer_call_insights(
            transcript_text=transcript_text,
            booking_status_msg=booking_status_msg,
            booking_detected=bool(agent_tools.booking_intent),
            sentiment=sentiment,
        )

        # Analytics timestamps (#19)
        ist = pytz.timezone("Asia/Kolkata")
        call_dt = call_start_time.astimezone(ist)

        # Stop recording
        recording_url = ""
        if egress_id:
            try:
                stop_api = api.LiveKitAPI(
                    url=os.environ["LIVEKIT_URL"],
                    api_key=os.environ["LIVEKIT_API_KEY"],
                    api_secret=os.environ["LIVEKIT_API_SECRET"],
                )
                await stop_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
                await stop_api.aclose()
                recording_url = (
                    f"{os.environ.get('SUPABASE_URL','')}/storage/v1/object/public/"
                    f"call-recordings/recordings/{ctx.room.name}.ogg"
                )
                logger.info(f"[RECORDING] Stopped. URL: {recording_url}")
            except Exception as e:
                err = str(e)
                if "EGRESS_COMPLETE" in err or "failed_precondition" in err:
                    logger.info("[RECORDING] Egress already completed before stop request.")
                else:
                    logger.warning(f"[RECORDING] Stop failed: {e}")

        # Update active_calls to completed (#38)
        await upsert_active_call("completed")

        # n8n webhook (#39)
        _n8n_url = os.getenv("N8N_WEBHOOK_URL")
        if _n8n_url:
            try:
                import httpx
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.post(_n8n_url, json={
                        "event":        "call_completed",
                        "phone":        caller_phone,
                        "caller_name":  agent_tools.caller_name,
                        "duration":     duration,
                        "booked":       bool(agent_tools.booking_intent),
                        "sentiment":    sentiment,
                        "summary":      booking_status_msg,
                        "recording_url":recording_url,
                        "interrupt_count": interrupt_count,
                    }, timeout=5.0)
                )
                logger.info("[N8N] Webhook triggered")
            except Exception as e:
                logger.warning(f"[N8N] Webhook failed: {e}")

        # Save to Supabase
        from db import save_call_log
        save_call_log(
            phone=caller_phone,
            duration=duration,
            transcript=transcript_text,
            summary=booking_status_msg,
            call_purpose=call_purpose,
            call_summary=call_summary,
            recording_url=recording_url,
            caller_name=agent_tools.caller_name or "",
            sentiment=sentiment,
            estimated_cost_usd=estimated_cost,
            call_date=call_dt.date().isoformat(),
            call_hour=call_dt.hour,
            call_day_of_week=call_dt.strftime("%A"),
            was_booked=bool(agent_tools.booking_intent),
            interrupt_count=interrupt_count,
        )

        # Backfill transcript rows so call_transcripts always has both user and assistant turns.
        if call_transcripts_enabled and transcript_text and transcript_text != "unavailable":
            try:
                sb = db.get_supabase()
                if sb:
                    existing = (
                        sb.table("call_transcripts")
                        .select("role")
                        .eq("call_room_id", ctx.room.name)
                        .execute()
                    )
                    existing_roles = {str(r.get("role", "")).lower() for r in (existing.data or [])}
                    rows = []
                    for line in transcript_text.splitlines():
                        line = line.strip()
                        if line.startswith("[USER] "):
                            role = "user"
                            content = line[len("[USER] "):].strip()
                        elif line.startswith("[ASSISTANT] "):
                            role = "assistant"
                            content = line[len("[ASSISTANT] "):].strip()
                        else:
                            continue
                        if not content:
                            continue
                        if existing_roles and "user" in existing_roles and role == "user":
                            continue
                        rows.append(
                            {
                                "call_room_id": ctx.room.name,
                                "phone": normalize_phone_number(caller_phone),
                                "role": role,
                                "content": content,
                            }
                        )
                    if rows:
                        sb.table("call_transcripts").insert(rows).execute()
            except Exception as e:
                err = str(e)
                if "PGRST205" in err and "call_transcripts" in err.lower():
                    logger.info("[TRANSCRIPT-BACKFILL] call_transcripts table missing; run supabase_migration_v2.sql.")
                else:
                    logger.debug(f"[TRANSCRIPT-BACKFILL] {e}")

    ctx.add_shutdown_callback(unified_shutdown_hook)


# ══════════════════════════════════════════════════════════════════════════════
# WORKER ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="outbound-caller",
    ))
