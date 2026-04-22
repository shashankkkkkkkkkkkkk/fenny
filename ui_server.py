import os, json, logging, re
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from phone_utils import normalize_phone_number, is_valid_phone_number, is_demo_phone

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="AI Voice Dashboard")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

IST = timezone(timedelta(hours=5, minutes=30))


def _norm_appt(value):
    if not value: return ""
    raw = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?$", raw, re.I)
    if m:
        y,mo,da,hh,mm,ss = m.groups(); ss = ss or "00"
        return f"{y}-{mo}-{da}T{hh}:{mm}:{ss}+05:30"
    try:
        n = raw.replace(" ","T")
        if n.endswith("Z"): n = n[:-1]+"+00:00"
        dt = datetime.fromisoformat(n)
        if dt.tzinfo: dt = dt.replace(tzinfo=None)
        return dt.replace(tzinfo=IST).isoformat(timespec="minutes")
    except: return raw


from config_manager import get_config as read_config

def write_config(data):
    logger.warning("Config writing is disabled. Please use environment variables.")
# Removed dict literal


# Removed old write_config implementation


def _set_supabase_env():
    cfg = read_config()
    for k,e in [("supabase_url","SUPABASE_URL"),("supabase_key","SUPABASE_KEY")]:
        v = cfg.get(k,""); 
        if v: os.environ[e] = v


# ── Config endpoints ──────────────────────────────────────────────────────────
@app.get("/api/config")
async def api_get_config(): return read_config()

@app.post("/api/config")
async def api_post_config(request: Request):
    write_config(await request.json())
    return {"status": "success"}


# ── Data endpoints ────────────────────────────────────────────────────────────
@app.get("/api/logs")
async def api_get_logs():
    _set_supabase_env()
    import db
    try:
        logs = db.fetch_call_logs(limit=50)
        cleaned = []
        for r in logs:
            phone = normalize_phone_number(r.get("phone_number"))
            if is_demo_phone(phone): continue
            r["phone_number"] = phone or r.get("phone_number")
            cleaned.append(r)
        return cleaned
    except Exception as e: logger.error(f"Logs error: {e}"); return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    _set_supabase_env()
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key: return PlainTextResponse(content="Error: Missing Supabase credentials in environment", status_code=500)
        sb = create_client(url, key)
        data = sb.table("call_logs").select("*").eq("id", log_id).single().execute().data
        text = f"Call Log — {data.get('created_at','')}\nPhone: {data.get('phone_number','Unknown')}\nDuration: {data.get('duration_seconds',0)}s\nSummary: {data.get('summary','')}\n\n--- TRANSCRIPT ---\n{data.get('transcript','No transcript.')}"
        return PlainTextResponse(content=text, headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"})
    except Exception as e: return PlainTextResponse(content=f"Error: {e}", status_code=500)

@app.get("/api/bookings")
async def api_get_bookings():
    _set_supabase_env()
    import db
    try:
        bookings = db.fetch_bookings()
        cleaned = []
        for b in bookings:
            phone = normalize_phone_number(b.get("phone_number")) or b.get("phone_number")
            if is_demo_phone(phone): continue
            r = dict(b); r["phone_number"] = phone
            r["appointment_time"] = _norm_appt(r.get("appointment_time"))
            cleaned.append(r)
        cleaned.sort(key=lambda x: x.get("appointment_time") or "", reverse=True)
        return cleaned
    except Exception as e: logger.error(f"Bookings error: {e}"); return []

@app.get("/api/stats")
async def api_get_stats():
    _set_supabase_env()
    import db
    try: return db.fetch_stats()
    except Exception as e: return {"total_calls":0,"total_bookings":0,"avg_duration":0,"booking_rate":0}

@app.get("/api/contacts")
async def api_get_contacts():
    _set_supabase_env()
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key: return []
        sb = create_client(url, key)
        try:
            rows = sb.table("call_logs").select("phone_number,caller_name,summary,call_purpose,call_summary,created_at,was_booked").order("created_at",desc=True).limit(500).execute().data or []
        except:
            rows = sb.table("call_logs").select("phone_number,caller_name,summary,created_at").order("created_at",desc=True).limit(500).execute().data or []
        contacts = {}
        for r in rows:
            phone = normalize_phone_number(r.get("phone_number") or "unknown")
            if is_demo_phone(phone): continue
            if phone not in contacts:
                contacts[phone] = {"phone_number":phone,"caller_name":r.get("caller_name") or "","total_calls":0,"last_seen":r.get("created_at"),"is_booked":False,"latest_purpose":r.get("call_purpose") or "","latest_summary":r.get("call_summary") or ""}
            c = contacts[phone]; c["total_calls"] += 1
            if not c["caller_name"] and r.get("caller_name"): c["caller_name"] = r["caller_name"]
            s = str(r.get("summary") or "").lower()
            if bool(r.get("was_booked")) or "confirm" in s or "already_signed_up" in s: c["is_booked"] = True
        return sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True)
    except Exception as e: logger.error(f"Contacts error: {e}"); return []

@app.get("/api/analytics")
async def api_get_analytics():
    _set_supabase_env()
    import db
    try:
        logs = [r for r in (db.fetch_call_logs(limit=500) or []) if not is_demo_phone(r.get("phone_number"))]
        total = len(logs); booked = 0; total_dur = 0; connected = 0
        by_day = {}; outcomes = {"booked":0,"cancelled":0,"completed":0,"unknown":0}
        for r in logs:
            s = (r.get("summary") or "").lower(); dur = int(r.get("duration_seconds") or 0)
            if dur > 0: total_dur += dur; connected += 1
            is_b = bool(r.get("was_booked")) or "confirm" in s or "already_signed_up" in s
            if is_b: booked += 1; outcomes["booked"] += 1
            elif "cancel" in s: outcomes["cancelled"] += 1
            elif s: outcomes["completed"] += 1
            else: outcomes["unknown"] += 1
            ca = r.get("created_at","")
            if ca: by_day[ca[:10]] = by_day.get(ca[:10],0)+1
        avg = round(total_dur/connected) if connected else 0
        b_rate = round((booked/total)*100,1) if total else 0.0
        c_rate = round((connected/total)*100,1) if total else 0.0
        daily = sorted([{"date":d,"calls":c} for d,c in by_day.items()], key=lambda x:x["date"])[-14:]
        return {"kpis":{"total_calls":total,"booked_calls":booked,"booking_rate":b_rate,"connect_rate":c_rate,"avg_duration":avg},"outcomes":outcomes,"daily_series":daily,"hourly_series":[]}
    except Exception as e:
        return {"kpis":{"total_calls":0,"booked_calls":0,"booking_rate":0.0,"connect_rate":0.0,"avg_duration":0},"outcomes":{"booked":0,"cancelled":0,"completed":0,"unknown":0},"daily_series":[],"hourly_series":[]}

@app.get("/api/patients")
async def api_get_patients():
    try:
        contacts = await api_get_contacts()
        patients = []
        for r in contacts:
            tc = int(r.get("total_calls") or 0); ib = bool(r.get("is_booked"))
            patients.append({"patient_id":(r.get("phone_number") or "unknown").replace("+",""),"name":r.get("caller_name") or "Unknown","phone_number":r.get("phone_number") or "unknown","total_calls":tc,"last_seen":r.get("last_seen"),"booked":ib,"engagement":"high" if tc>=5 else ("medium" if tc>=2 else "low"),"status":"active" if ib or tc>0 else "new"})
        return patients
    except Exception as e: return []


# ── Outbound calls ────────────────────────────────────────────────────────────
@app.post("/api/call/single")
async def api_call_single(request: Request):
    data = await request.json()
    phone = normalize_phone_number((data.get("phone") or "").strip())
    if not is_valid_phone_number(phone): return {"status":"error","message":"Invalid phone number."}
    cfg = read_config()
    try:
        import random, json as _j
        from livekit import api as lkapi
        lk = lkapi.LiveKitAPI(url=cfg.get("livekit_url") or os.environ.get("LIVEKIT_URL",""), api_key=cfg.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY",""), api_secret=cfg.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET",""))
        room = f"call-{phone.replace('+','')}-{random.randint(1000,9999)}"
        d = await lk.agent_dispatch.create_dispatch(lkapi.CreateAgentDispatchRequest(agent_name="outbound-caller",room=room,metadata=_j.dumps({"phone_number":phone})))
        await lk.aclose()
        return {"status":"ok","dispatch_id":d.id,"room":room,"phone":phone}
    except Exception as e: return {"status":"error","message":str(e)}

@app.post("/api/call/bulk")
async def api_call_bulk(request: Request):
    import random, json as _j
    from livekit import api as lkapi
    data = await request.json()
    numbers = [normalize_phone_number(n.strip()) for n in (data.get("numbers") or "").splitlines() if n.strip()]
    cfg = read_config(); results = []
    lk_url = cfg.get("livekit_url") or os.environ.get("LIVEKIT_URL","")
    lk_key = cfg.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY","")
    lk_sec = cfg.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET","")
    for phone in numbers:
        if not is_valid_phone_number(phone): results.append({"phone":phone,"status":"error","message":"Invalid number"}); continue
        try:
            lk = lkapi.LiveKitAPI(url=lk_url,api_key=lk_key,api_secret=lk_sec)
            room = f"call-{phone.replace('+','')}-{random.randint(1000,9999)}"
            d = await lk.agent_dispatch.create_dispatch(lkapi.CreateAgentDispatchRequest(agent_name="outbound-caller",room=room,metadata=_j.dumps({"phone_number":phone})))
            await lk.aclose(); results.append({"phone":phone,"status":"ok","dispatch_id":d.id})
        except Exception as e: results.append({"phone":phone,"status":"error","message":str(e)})
    return {"results":results,"total":len(results)}


# ── Health + SPA ──────────────────────────────────────────────────────────────
@app.get("/health")
def health(): return {"status":"ok","timestamp":datetime.utcnow().isoformat(),"service":"voice-agent"}

@app.get("/", response_class=HTMLResponse)
async def root():
    idx = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(idx):
        with open(idx, "r", encoding="utf-8") as f: return HTMLResponse(f.read())
    return HTMLResponse("<h1>AI Voice Agent</h1><p>Frontend not found. Place files in /frontend.</p>")
