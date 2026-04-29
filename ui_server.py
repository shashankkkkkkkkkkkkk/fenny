import os, logging, re
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from phone_utils import normalize_phone_number, is_valid_phone_number, is_demo_phone

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="Sri Aakrithis AI Dashboard")
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

IST = timezone(timedelta(hours=5, minutes=30))

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def _get_sb():
    """Return a Supabase client or None if not configured."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        logger.error(f"Supabase init error: {e}")
        return None

from config_manager import get_config

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "service": "aria-dashboard"}

# ── Config ────────────────────────────────────────────────────────────────────
@app.get("/api/config")
async def api_get_config():
    cfg = get_config()
    # Redact secrets before sending to frontend
    safe = {k: v for k, v in cfg.items() if "key" not in k.lower() and "secret" not in k.lower()}
    return safe

# ── Logs ──────────────────────────────────────────────────────────────────────
@app.get("/api/logs")
async def api_get_logs():
    import db
    try:
        logs = db.fetch_call_logs(limit=100)
        return [
            {**r, "phone_number": normalize_phone_number(r.get("phone_number")) or r.get("phone_number")}
            for r in (logs or [])
            if not is_demo_phone(normalize_phone_number(r.get("phone_number")))
        ]
    except Exception as e:
        logger.error(f"Logs error: {e}")
        return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    sb = _get_sb()
    if not sb:
        return PlainTextResponse("Supabase not configured.", status_code=503)
    try:
        data = sb.table("call_logs").select("*").eq("id", log_id).single().execute().data or {}
        text = (
            f"Call Log — {data.get('created_at','')}\n"
            f"Phone: {data.get('phone_number','Unknown')}\n"
            f"Caller: {data.get('caller_name','Unknown')}\n"
            f"Duration: {data.get('duration_seconds',0)}s\n"
            f"Summary: {data.get('summary','')}\n\n"
            f"--- TRANSCRIPT ---\n{data.get('transcript','No transcript available.')}"
        )
        return PlainTextResponse(content=text, headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"})
    except Exception as e:
        return PlainTextResponse(f"Error: {e}", status_code=500)

# ── Bookings ──────────────────────────────────────────────────────────────────
@app.get("/api/bookings")
async def api_get_bookings():
    import db
    try:
        bookings = db.fetch_bookings() or []
        cleaned = []
        for b in bookings:
            phone = normalize_phone_number(b.get("phone_number")) or b.get("phone_number")
            if is_demo_phone(phone): continue
            r = dict(b)
            r["phone_number"] = phone
            r["appointment_time"] = _norm_appt(r.get("appointment_time"))
            cleaned.append(r)
        cleaned.sort(key=lambda x: x.get("appointment_time") or "", reverse=True)
        return cleaned
    except Exception as e:
        logger.error(f"Bookings error: {e}")
        return []

# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def api_get_stats():
    import db
    try:
        return db.fetch_stats()
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}

# ── Contacts ──────────────────────────────────────────────────────────────────
@app.get("/api/contacts")
async def api_get_contacts():
    sb = _get_sb()
    if not sb:
        return []
    try:
        try:
            rows = sb.table("call_logs").select(
                "phone_number,caller_name,summary,call_purpose,call_summary,created_at,was_booked"
            ).order("created_at", desc=True).limit(500).execute().data or []
        except Exception:
            rows = sb.table("call_logs").select(
                "phone_number,caller_name,summary,created_at"
            ).order("created_at", desc=True).limit(500).execute().data or []

        contacts: dict = {}
        for r in rows:
            phone = normalize_phone_number(r.get("phone_number") or "unknown")
            if is_demo_phone(phone): continue
            if phone not in contacts:
                contacts[phone] = {
                    "phone_number": phone,
                    "caller_name": r.get("caller_name") or "",
                    "total_calls": 0,
                    "last_seen": r.get("created_at"),
                    "is_booked": False,
                    "latest_purpose": r.get("call_purpose") or "",
                    "latest_summary": r.get("call_summary") or "",
                }
            c = contacts[phone]
            c["total_calls"] += 1
            if not c["caller_name"] and r.get("caller_name"):
                c["caller_name"] = r["caller_name"]
            s = str(r.get("summary") or "").lower()
            if bool(r.get("was_booked")) or "confirm" in s or "already_signed_up" in s:
                c["is_booked"] = True
        return sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True)
    except Exception as e:
        logger.error(f"Contacts error: {e}")
        return []

# ── Analytics ─────────────────────────────────────────────────────────────────
@app.get("/api/analytics")
async def api_get_analytics():
    import db
    try:
        logs = [r for r in (db.fetch_call_logs(limit=500) or []) if not is_demo_phone(r.get("phone_number"))]
        total = len(logs); booked = 0; total_dur = 0; connected = 0
        by_day: dict = {}
        outcomes = {"booked": 0, "completed": 0, "cancelled": 0, "unknown": 0}
        for r in logs:
            s   = (r.get("summary") or "").lower()
            dur = int(r.get("duration_seconds") or 0)
            if dur > 0: total_dur += dur; connected += 1
            is_b = bool(r.get("was_booked")) or "confirm" in s or "already_signed_up" in s
            if is_b:           booked += 1; outcomes["booked"] += 1
            elif "cancel" in s: outcomes["cancelled"] += 1
            elif s:             outcomes["completed"] += 1
            else:               outcomes["unknown"] += 1
            ca = r.get("created_at", "")
            if ca: by_day[ca[:10]] = by_day.get(ca[:10], 0) + 1
        avg    = round(total_dur / connected) if connected else 0
        b_rate = round((booked / total) * 100, 1) if total else 0.0
        c_rate = round((connected / total) * 100, 1) if total else 0.0
        daily  = sorted([{"date": d, "calls": c} for d, c in by_day.items()], key=lambda x: x["date"])[-14:]
        return {
            "kpis": {"total_calls": total, "booked_calls": booked, "booking_rate": b_rate, "connect_rate": c_rate, "avg_duration": avg},
            "outcomes": outcomes,
            "daily_series": daily,
        }
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        return {"kpis": {"total_calls":0,"booked_calls":0,"booking_rate":0.0,"connect_rate":0.0,"avg_duration":0},
                "outcomes": {"booked":0,"cancelled":0,"completed":0,"unknown":0}, "daily_series": []}

# ── SPA fallback ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/{path:path}", response_class=HTMLResponse)
async def spa(path: str = ""):
    idx = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(idx):
        with open(idx, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Aria Dashboard</h1><p>Frontend not found.</p>")
