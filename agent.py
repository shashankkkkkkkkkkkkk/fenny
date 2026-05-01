import os, json, logging, certifi, pytz, re, asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Annotated

os.environ["SSL_CERT_FILE"] = certifi.where()
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
load_dotenv()
logger = logging.getLogger("voice-agent")
logging.basicConfig(level=logging.INFO)

from livekit import api
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions, WorkerOptions, cli, llm
from livekit.plugins import openai, sarvam, silero

from config_manager import get_config as get_live_config

def count_tokens(text):
    try:
        import tiktoken
        return len(tiktoken.encoding_for_model("gpt-4o").encode(text))
    except Exception:
        return len(text.split())

def get_ist_time_context():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    days_lines = []
    for i in range(7):
        day = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} -> ISO {day.strftime('%Y-%m-%d')}")
    return (
        f"\n\n[SYSTEM CONTEXT]\nNow: {now.strftime('%A, %B %d, %Y')} at {now.strftime('%I:%M %p')} IST\n"
        f"Day reference table:\n" + "\n".join(days_lines) +
        "\nUse ISO dates in save_booking_intent. Booking window: 9AM-9PM IST only.]"
    )

LANG_PRESETS = {
    "hinglish":    {"tts_language":"hi-IN","tts_voice":"kavya","instruction":"Speak natural Hinglish — mix Hindi and English."},
    "hindi":       {"tts_language":"hi-IN","tts_voice":"ritu","instruction":"Speak only pure Hindi."},
    "english":     {"tts_language":"en-IN","tts_voice":"dev","instruction":"Speak Indian English, warm and professional."},
    "tamil":       {"tts_language":"ta-IN","tts_voice":"priya","instruction":"Speak only Tamil."},
    "telugu":      {"tts_language":"te-IN","tts_voice":"kavya","instruction":"Speak only Telugu."},
    "gujarati":    {"tts_language":"gu-IN","tts_voice":"rohan","instruction":"Speak only Gujarati."},
    "bengali":     {"tts_language":"bn-IN","tts_voice":"neha","instruction":"Speak only Bengali."},
    "marathi":     {"tts_language":"mr-IN","tts_voice":"shubh","instruction":"Speak only Marathi."},
    "kannada":     {"tts_language":"kn-IN","tts_voice":"rahul","instruction":"Speak only Kannada."},
    "malayalam":   {"tts_language":"ml-IN","tts_voice":"ritu","instruction":"Speak only Malayalam."},
    "multilingual":{"tts_language":"hi-IN","tts_voice":"kavya","instruction":"Detect caller language from first message and reply in that language. Supported: Hindi, Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, Kannada, Malayalam."},
}

TTS_SCRIPT = {"hi-IN":r"[\u0900-\u097F]","mr-IN":r"[\u0900-\u097F]","bn-IN":r"[\u0980-\u09FF]","gu-IN":r"[\u0A80-\u0AFF]","ta-IN":r"[\u0B80-\u0BFF]","te-IN":r"[\u0C00-\u0C7F]","kn-IN":r"[\u0C80-\u0CFF]","ml-IN":r"[\u0D00-\u0D7F]"}
TTS_PREFIX = {"hi-IN":"नमस्ते","mr-IN":"नमस्कार","bn-IN":"নমস্কার","gu-IN":"નમસ્તે","ta-IN":"வணக்கம்","te-IN":"నమస్కారం","kn-IN":"ನಮಸ್ಕಾರ","ml-IN":"നമസ്കാരം"}

def enforce_tts(text, lang):
    t = (text or "").replace("—","-").strip() or "Hello."
    p = TTS_SCRIPT.get(lang)
    if not p or re.search(p, t): return t
    pfx = TTS_PREFIX.get(lang,"")
    return f"{pfx}. {t}" if pfx else t

def norm_time(start_time):
    raw = str(start_time or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?$", raw, re.I)
    if m:
        y,mo,da,hh,mm,ss = m.groups(); ss=ss or "00"
        return f"{y}-{mo}-{da}T{hh}:{mm}:{ss}+05:30"
    return raw

def infer_insights(transcript, booking_msg, booked, sentiment):
    tl = (transcript or "").lower()
    if booked or "booking confirmed" in (booking_msg or "").lower() or "appointment" in tl:
        purpose = "Appointment booking"
    elif "cancel" in tl or "resched" in tl: purpose = "Appointment update"
    elif any(k in tl for k in ("price","cost","timing","hours","location")): purpose = "General inquiry"
    elif any(k in tl for k in ("pain","urgent","emergency","help")): purpose = "Support request"
    else: purpose = "General conversation"
    first_user = next((l[7:].strip() for l in (transcript or "").splitlines() if l.startswith("[USER] ")), "")
    summary = f"Caller: {first_user[:200]}. Outcome: {booking_msg}. Sentiment: {sentiment}." if first_user else f"Purpose: {purpose}. Outcome: {booking_msg}. Sentiment: {sentiment}."
    return purpose, summary

from calendar_tools import get_available_slots, async_create_booking, is_within_booking_hours_ist, get_calendar_setup_error
import db
from phone_utils import normalize_phone_number, is_valid_phone_number, phone_match_variants

class AgentTools(llm.ToolContext):
    def __init__(self, caller_phone, caller_name=""):
        super().__init__(tools=[])
        self.caller_phone = caller_phone
        self.caller_name = caller_name
        self.booking_intent = None
        self.sip_domain = os.getenv("VOBIZ_SIP_DOMAIN","")
        self.ctx_api = None
        self.room_name = None
        self._sip_identity = None

    @llm.function_tool(description="Transfer this call to a human agent. Use if caller asks for human, is angry, or query is outside scope.")
    async def transfer_call(self, context: Annotated[str,"Optional context"]="") -> str:
        dest = os.getenv("DEFAULT_TRANSFER_NUMBER","")
        if dest and self.sip_domain and "@" not in dest:
            dest = f"sip:{dest.replace('tel:','').replace('sip:','')}@{self.sip_domain}"
        if dest and not dest.startswith("sip:"): dest = f"sip:{dest}"
        try:
            if self.ctx_api and self.room_name and dest and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(room_name=self.room_name,participant_identity=self._sip_identity,transfer_to=dest,play_dialtone=False))
                return "Transfer initiated."
        except Exception as e: logger.error(f"Transfer failed: {e}")
        return "Unable to transfer right now."

    @llm.function_tool(description="End the call. Use ONLY when caller says bye or after booking fully confirmed.")
    async def end_call(self, reason: Annotated[str,"Optional reason"]="") -> str:
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(room_name=self.room_name,participant_identity=self._sip_identity,transfer_to="tel:+00000000",play_dialtone=False))
        except Exception as e: logger.warning(f"[END-CALL] {e}")
        return "Call ended."

    @llm.function_tool(description="Save booking intent once caller confirms name, phone, date and time.")
    async def save_booking_intent(self, start_time:Annotated[str,"ISO 8601 e.g. 2026-03-01T10:00:00+05:30"], caller_name:Annotated[str,"Full name"], caller_phone:Annotated[str,"Phone number"], notes:Annotated[str,"Notes"]="") -> str:
        try:
            t = norm_time(start_time)
            if not is_within_booking_hours_ist(t): return "Appointments only 9AM-9PM IST. Please choose a time in that range."
            self.booking_intent = {"start_time":t,"caller_name":caller_name,"caller_phone":caller_phone,"notes":notes}
            self.caller_name = caller_name
            return f"Booking saved for {caller_name} at {t}. I will confirm after the call."
        except Exception as e: return "Trouble saving booking. Try again."

    @llm.function_tool(description="Check available appointment slots for a date.")
    async def check_availability(self, date:Annotated[str,"YYYY-MM-DD"]) -> str:
        try:
            err = get_calendar_setup_error()
            if err: return f"{err} Please configure calendar."
            slots = get_available_slots(date)
            if not slots: return f"No slots on {date}. Check another date?"
            labels = [s.get("label") or s.get("time","") for s in slots[:6] if isinstance(s,dict)]
            return f"Available on {date}: {', '.join(labels)} IST."
        except Exception as e: return "Trouble checking calendar right now."

    @llm.function_tool(description="Check if the business is currently open.")
    async def get_business_hours(self, context:Annotated[str,"Optional"]="") -> str:
        ist = pytz.timezone("Asia/Kolkata"); now = datetime.now(ist)
        t = now.strftime("%H:%M"); day = now.strftime("%A")
        return f"OPEN. {day}: 09:00-21:00 IST." if "09:00"<=t<"21:00" else f"CLOSED. {day}: 09:00-21:00 IST."


class VoiceAssistant(Agent):
    def __init__(self, agent_tools, live_config):
        self._live_config = live_config
        self._agent_tools = agent_tools
        base = live_config.get("agent_instructions","")
        lang_preset = live_config.get("lang_preset","multilingual")
        preset = LANG_PRESETS.get(lang_preset, LANG_PRESETS["multilingual"])
        tts_lang = live_config.get("tts_language", preset["tts_language"])
        tool_note = "\n\n[TOOL SAFETY] Call tools only with valid args. Never invent missing fields. Ask caller if anything missing."
        tts_note = ""
        if tts_lang in TTS_SCRIPT:
            pfx = TTS_PREFIX.get(tts_lang,"नमस्ते")
            tts_note = f"\n\n[TTS] Always include one native-script word per reply. Prepend '{pfx}' when needed."
        instructions = base + get_ist_time_context() + f"\n\n[LANGUAGE]\n{preset['instruction']}" + tts_note + tool_note
        tok = count_tokens(instructions)
        logger.info(f"[PROMPT] {tok} tokens")
        if tok > 600: logger.warning("[PROMPT] >600 tokens — consider trimming")
        super().__init__(instructions=instructions, tools=llm.find_function_tools(agent_tools))

    async def on_enter(self):
        greeting = self._live_config.get("first_line","Hello! How can I help you today?")
        greeting = enforce_tts(greeting, self._live_config.get("tts_language","en-IN"))
        await (self.session.say(greeting, add_to_chat_ctx=True)).wait_for_playout()


agent_is_speaking = False

async def entrypoint(ctx: JobContext):
    global agent_is_speaking
    shutdown_done = False
    shutdown_lock = asyncio.Lock()

    await ctx.connect()
    logger.info(f"[ROOM] {ctx.room.name}")

    live_config = get_live_config()
    caller_name = ""; caller_phone = "unknown"; participant_phone = None

    for identity, participant in ctx.room.remote_participants.items():
        if participant.name and participant.name not in ("","Caller","Unknown"): caller_name = participant.name
        if not participant_phone:
            attr = participant.attributes or {}
            participant_phone = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if not participant_phone and "+" in identity:
            m = re.search(r"\+\d{7,15}", identity)
            if m: participant_phone = m.group()

    dispatch_phone = None
    if ctx.job.metadata:
        try: dispatch_phone = json.loads(ctx.job.metadata).get("phone_number")
        except: pass

    caller_phone = normalize_phone_number(dispatch_phone or participant_phone or "unknown")
    is_outbound = bool(dispatch_phone and dispatch_phone not in ("demo",""))

    if is_outbound:
        trunk_id = live_config.get("sip_trunk_id","") or os.getenv("SIP_TRUNK_ID","")
        dial_to = normalize_phone_number(caller_phone)
        if not trunk_id: logger.error("[DIAL] Missing sip_trunk_id"); return
        if not is_valid_phone_number(dial_to): logger.error(f"[DIAL] Invalid number: {dial_to}"); return
        try:
            await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(sip_trunk_id=trunk_id,sip_call_to=dial_to,room_name=ctx.room.name,participant_identity=f"sip_{dial_to.replace('+','')}",participant_name=dial_to,play_dialtone=False,wait_until_answered=False))
            caller_phone = dial_to
        except Exception as e: logger.error(f"[DIAL] {e}"); return

    # Caller memory
    async def get_history(phone):
        if phone == "unknown": return ""
        try:
            sb = db.get_supabase()
            if not sb: return ""
            variants = phone_match_variants(phone)
            q = sb.table("call_logs").select("summary,created_at").order("created_at",desc=True).limit(1)
            q = q.in_("phone_number",variants) if variants else q.eq("phone_number",phone)
            r = q.execute()
            if r.data: return f"\n\n[CALLER HISTORY: Last call {r.data[0]['created_at'][:10]}. {r.data[0]['summary']}]"
        except: pass
        return ""

    history = await get_history(caller_phone)
    if history: live_config["agent_instructions"] = live_config.get("agent_instructions","") + history

    agent_tools = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    agent_tools._sip_identity = f"sip_{caller_phone.replace('+','')}" if caller_phone != "unknown" else "inbound_caller"
    agent_tools.ctx_api = ctx.api
    agent_tools.room_name = ctx.room.name

    # LLM: Gemini primary, Groq fallback
    gemini_key = os.environ.get("GEMINI_API_KEY","") or os.environ.get("GOOGLE_API_KEY","")
    groq_key = os.environ.get("GROQ_API_KEY","")
    max_tok = max(40, min(int(live_config.get("llm_max_completion_tokens",80)), 160))
    candidates = []
    if gemini_key:
        candidates.append(openai.LLM(model=live_config.get("gemini_model","gemini-2.0-flash"), base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=gemini_key, max_completion_tokens=max_tok))
    if groq_key:
        candidates.append(openai.LLM(model=live_config.get("groq_model","llama-3.3-70b-versatile"), base_url="https://api.groq.com/openai/v1", api_key=groq_key, max_completion_tokens=max_tok))
    if not candidates: logger.error("[LLM] No provider configured."); return
    agent_llm = candidates[0] if len(candidates)==1 else llm.FallbackAdapter(llm=candidates,attempt_timeout=4.0,max_retry_per_llm=0,retry_interval=0.2,retry_on_chunk_sent=False)
    logger.info(f"[LLM] Providers: {len(candidates)}")

    tts_voice = live_config.get("tts_voice","kavya")
    tts_lang = live_config.get("tts_language","en-IN")
    stt_lang = live_config.get("stt_language","unknown")
    max_turns = int(live_config.get("max_turns",30))
    min_ep = max(0.05, min(float(live_config.get("stt_min_endpointing_delay",0.08)),0.20))
    max_ep = max(min_ep, min(float(live_config.get("stt_max_endpointing_delay",3.5)),3.5))

    agent_stt = sarvam.STT(language=stt_lang, model="saaras:v3", mode="translate", flush_signal=True)
    agent_tts = sarvam.TTS(target_language_code=tts_lang, model="bulbul:v3", speaker=tts_voice, enable_preprocessing=True)
    agent_vad = silero.VAD.load(min_speech_duration=0.1, min_silence_duration=1.0)

    agent = VoiceAssistant(agent_tools=agent_tools, live_config=live_config)
    session = AgentSession(
        stt=agent_stt, 
        llm=agent_llm, 
        tts=agent_tts, 
        vad=agent_vad,
        allow_interruptions=True
    )
    await session.start(room=ctx.room, agent=agent, room_input_options=RoomInputOptions(close_on_disconnect=False))
    logger.info("[AGENT] Session live.")

    call_start = datetime.now()
    interrupt_count = 0; turn_count = 0

    # Recording
    egress_id = None; recording_url = ""
    s3_key = os.environ.get("SUPABASE_S3_ACCESS_KEY","")
    s3_secret = os.environ.get("SUPABASE_S3_SECRET_KEY","")
    s3_endpoint = os.environ.get("SUPABASE_S3_ENDPOINT","")
    if s3_key and s3_secret and s3_endpoint:
        try:
            rec_api = api.LiveKitAPI(url=os.environ["LIVEKIT_URL"], api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"])
            resp = await rec_api.egress.start_room_composite_egress(api.RoomCompositeEgressRequest(room_name=ctx.room.name,audio_only=True,file_outputs=[api.EncodedFileOutput(file_type=api.EncodedFileType.OGG,filepath=f"recordings/{ctx.room.name}.ogg",s3=api.S3Upload(access_key=s3_key,secret=s3_secret,bucket="call-recordings",region=os.environ.get("SUPABASE_S3_REGION","ap-south-1"),endpoint=s3_endpoint,force_path_style=True))]))
            egress_id = resp.egress_id
            base_url = os.environ.get("SUPABASE_URL","").rstrip("/")
            recording_url = f"{base_url}/storage/v1/object/public/call-recordings/recordings/{ctx.room.name}.ogg"
            await rec_api.aclose()
            logger.info(f"[REC] Started: {egress_id}")
        except Exception as e: logger.warning(f"[REC] {e}")

    FILLERS = {"okay.","okay","ok","uh","hmm","hm","yeah","yes","no","um","ah","oh","right","sure","fine","good","haan","han","theek","accha","ji","ha"}

    @session.on("agent_speech_started")
    def _on_start(ev):
        global agent_is_speaking; agent_is_speaking = True

    @session.on("agent_speech_finished")
    def _on_end(ev):
        global agent_is_speaking; agent_is_speaking = False

    @session.on("agent_speech_interrupted")
    def _on_interrupt(ev):
        nonlocal interrupt_count; interrupt_count += 1

    call_transcripts_enabled = True

    async def log_transcript(role, content):
        nonlocal call_transcripts_enabled
        if not call_transcripts_enabled: return
        try:
            sb = db.get_supabase()
            if sb:
                sb.table("call_transcripts").insert({"call_room_id":ctx.room.name,"phone":normalize_phone_number(caller_phone),"role":role,"content":content}).execute()
        except Exception as e:
            if "PGRST205" in str(e): call_transcripts_enabled = False

    @session.on("user_speech_committed")
    def _on_speech(ev):
        nonlocal turn_count
        global agent_is_speaking
        t = ev.user_transcript.strip()
        if agent_is_speaking or not t or len(t)<3 or t.lower().rstrip(".") in FILLERS: return
        asyncio.create_task(log_transcript("user", t))
        turn_count += 1
        logger.info(f"[TURN] {turn_count}/{max_turns}: {t!r}")
        if turn_count >= max_turns:
            asyncio.create_task(session.generate_reply(instructions="Politely wrap up, thank caller, say goodbye."))

    @ctx.room.on("participant_disconnected")
    def _on_disconnect(participant):
        global agent_is_speaking; agent_is_speaking = False
        asyncio.create_task(_shutdown(ctx))

    async def _shutdown(shutdown_ctx):
        nonlocal shutdown_done
        async with shutdown_lock:
            if shutdown_done: return
            shutdown_done = True

        duration = int((datetime.now()-call_start).total_seconds())
        booking_msg = "No booking"; appt_time = None

        if agent_tools.booking_intent:
            try:
                intent = agent_tools.booking_intent; appt_time = intent["start_time"]
                result = await async_create_booking(start_time=intent["start_time"],caller_name=intent["caller_name"] or "Unknown",caller_phone=intent["caller_phone"],notes=intent["notes"])
                if not result.get("success") and "timed out" in str(result.get("message","")).lower():
                    result = await async_create_booking(start_time=intent["start_time"],caller_name=intent["caller_name"] or "Unknown",caller_phone=intent["caller_phone"],notes=intent["notes"])
                booking_msg = f"Booking Confirmed: {result.get('booking_id')}" if result.get("success") else f"Booking Failed: {result.get('message')}"
            except Exception as e: booking_msg = f"Booking Failed: {e}"

        # Build transcript
        transcript = "unavailable"
        try:
            msgs = agent.chat_ctx.messages
            if callable(msgs): msgs = msgs()
            lines = []
            for m in msgs:
                if getattr(m,"role",None) in ("user","assistant"):
                    c = getattr(m,"content","")
                    if isinstance(c,list): c = " ".join(str(x) for x in c if isinstance(x,str))
                    lines.append(f"[{m.role.upper()}] {c}")
            transcript = "\n".join(lines)
        except Exception as e: logger.error(f"[TRANSCRIPT] {e}")

        # Backfill transcripts
        if call_transcripts_enabled and transcript != "unavailable":
            try:
                sb = db.get_supabase()
                if sb:
                    rows = []
                    for line in transcript.splitlines():
                        if line.startswith("[USER] "): rows.append({"call_room_id":ctx.room.name,"phone":normalize_phone_number(caller_phone),"role":"user","content":line[7:].strip()})
                        elif line.startswith("[ASSISTANT] "): rows.append({"call_room_id":ctx.room.name,"phone":normalize_phone_number(caller_phone),"role":"assistant","content":line[12:].strip()})
                    if rows: sb.table("call_transcripts").insert(rows).execute()
            except: pass

        # Sentiment
        tl = transcript.lower()
        if any(k in tl for k in ("frustrated","upset","angry","irritated")): sentiment = "frustrated"
        elif any(k in tl for k in ("not interested","bad","problem","issue")): sentiment = "negative"
        elif any(k in tl for k in ("thanks","great","good","yes","confirmed","booked")): sentiment = "positive"
        else: sentiment = "neutral"

        purpose, call_summary = infer_insights(transcript, booking_msg, bool(agent_tools.booking_intent), sentiment)
        ist = pytz.timezone("Asia/Kolkata"); cdt = call_start.astimezone(ist)
        cost = round((duration/60)*0.008 + (len(transcript)/1000)*0.003, 5)

        # Stop recording
        if egress_id:
            try:
                stop_api = api.LiveKitAPI(url=os.environ["LIVEKIT_URL"],api_key=os.environ["LIVEKIT_API_KEY"],api_secret=os.environ["LIVEKIT_API_SECRET"])
                await stop_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
                await stop_api.aclose()
            except Exception as e:
                if "EGRESS_COMPLETE" not in str(e) and "failed_precondition" not in str(e): logger.warning(f"[REC] Stop: {e}")

        db.save_call_log(phone=caller_phone,duration=duration,transcript=transcript,summary=booking_msg,appointment_time=appt_time,call_purpose=purpose,call_summary=call_summary,recording_url=recording_url,caller_name=agent_tools.caller_name or "",sentiment=sentiment,estimated_cost_usd=cost,call_date=cdt.date().isoformat(),call_hour=cdt.hour,call_day_of_week=cdt.strftime("%A"),was_booked=bool(agent_tools.booking_intent),interrupt_count=interrupt_count)
        logger.info(f"[SHUTDOWN] Done. Duration={duration}s Booked={bool(agent_tools.booking_intent)}")

    ctx.add_shutdown_callback(_shutdown)


if __name__ == "__main__":
    import sys

    lk_url    = os.environ.get("LIVEKIT_URL", "")
    lk_key    = os.environ.get("LIVEKIT_API_KEY", "")
    lk_secret = os.environ.get("LIVEKIT_API_SECRET", "")

    print(f"[agent] LIVEKIT_URL  : '{lk_url[:40] if lk_url else 'NOT SET'}'")
    print(f"[agent] API_KEY      : '{'SET' if lk_key else 'NOT SET'}'")
    print(f"[agent] API_SECRET   : '{'SET' if lk_secret else 'NOT SET'}'")

    if not lk_url or not lk_key or not lk_secret:
        print("[agent] FATAL: LiveKit credentials missing. Check Railway Variables.")
        sys.exit(1)

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=lk_url,
            api_key=lk_key,
            api_secret=lk_secret,
        )
    )

