import os, logging, requests, httpx, asyncio
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("calendar-tools")

CAL_BASE = "https://api.cal.com/v1"
IST = timezone(timedelta(hours=5, minutes=30))
BOOKING_START_HOUR = 9
BOOKING_END_HOUR = 21


def _format_ampm(dt): return dt.strftime("%I:%M %p").lstrip("0")

def _to_ist(value):
    from datetime import datetime as dt
    if isinstance(value, dt): d = value
    else:
        raw = str(value or "").strip().replace(" ","T")
        if raw.endswith("Z"): raw = raw[:-1]+"+00:00"
        try: d = dt.fromisoformat(raw)
        except: return None
    if d.tzinfo is None: d = d.replace(tzinfo=IST)
    return d.astimezone(IST)

def is_within_booking_hours_ist(value):
    d = _to_ist(value)
    if not d: return False
    mins = d.hour*60+d.minute
    return BOOKING_START_HOUR*60 <= mins < BOOKING_END_HOUR*60

def get_cal_creds():
    raw = str(os.environ.get("CAL_EVENT_TYPE_ID","0") or "0").strip()
    try: eid = int(raw)
    except: eid = 0; logger.error(f"[CAL] Invalid CAL_EVENT_TYPE_ID='{raw}'")
    return {"api_key": os.environ.get("CAL_API_KEY",""), "event_id": eid}

def get_calendar_setup_error():
    creds = get_cal_creds()
    if not creds["api_key"]: return "Calendar setup missing: CAL_API_KEY not configured."
    if not creds["event_id"]: return "Calendar setup missing: CAL_EVENT_TYPE_ID missing or invalid."
    return ""

def get_available_slots(date_str):
    err = get_calendar_setup_error()
    if err: logger.error(f"[CAL] {err}"); return []
    creds = get_cal_creds()
    try:
        resp = requests.get(f"{CAL_BASE}/slots", headers={"Content-Type":"application/json"},
            params={"apiKey":creds["api_key"],"eventTypeId":creds["event_id"],"startTime":f"{date_str}T00:00:00.000Z","endTime":f"{date_str}T23:59:59.000Z"}, timeout=8)
        resp.raise_for_status()
        raw_slots = resp.json().get("data",{}).get("slots",{}).get(date_str,[])
        slots = []
        for s in raw_slots:
            d = _to_ist(s["time"])
            if not d or not is_within_booking_hours_ist(d): continue
            slots.append({"time":s["time"],"label":_format_ampm(d)})
        logger.info(f"[CAL] {len(slots)} slots for {date_str}")
        return slots
    except Exception as e: logger.error(f"[CAL] get_available_slots: {e}"); return []

def create_booking(start_time, caller_name, caller_phone, notes=""):
    try: return asyncio.get_event_loop().run_until_complete(async_create_booking(start_time,caller_name,caller_phone,notes))
    except RuntimeError: return asyncio.run(async_create_booking(start_time,caller_name,caller_phone,notes))

async def async_create_booking(start_time, caller_name, caller_phone, notes=""):
    if not is_within_booking_hours_ist(start_time):
        return {"success":False,"booking_id":None,"message":"Appointments only 9AM-9PM IST."}
    return await _create_booking_calcom(start_time, caller_name, caller_phone, notes)

async def _create_booking_calcom(start_time, caller_name, caller_phone, notes):
    creds = get_cal_creds()
    if not creds["api_key"]: return {"success":False,"booking_id":None,"message":"CAL_API_KEY missing."}
    if not creds["event_id"]: return {"success":False,"booking_id":None,"message":"CAL_EVENT_TYPE_ID missing."}
    payload = {
        "eventTypeId": creds["event_id"], "start": start_time,
        "attendee": {"name":caller_name,"email":f"{caller_phone.replace('+','').replace(' ','')}@voiceagent.placeholder","phoneNumber":caller_phone,"timeZone":"Asia/Kolkata","language":"en"},
        "bookingFieldsResponses": {"notes": notes or f"Booked via AI voice agent. Phone: {caller_phone}"},
    }
    headers = {"Authorization":f"Bearer {creds['api_key']}","cal-api-version":"2024-08-13","Content-Type":"application/json"}
    for attempt in range(1,4):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post("https://api.cal.com/v2/bookings", headers=headers, json=payload)
            if resp.status_code in (200,201):
                uid = resp.json().get("data",{}).get("uid","unknown")
                logger.info(f"[CAL] Booking created: {uid}")
                return {"success":True,"booking_id":uid,"message":"Booking confirmed"}
            if resp.status_code==409 and "already_signed_up" in resp.text:
                return {"success":True,"booking_id":"already-signed-up","message":"Booking already exists."}
            logger.error(f"[CAL] {resp.status_code}: {resp.text}")
            return {"success":False,"booking_id":None,"message":resp.text}
        except httpx.TimeoutException:
            if attempt<3: await asyncio.sleep(float(attempt)); continue
            return {"success":False,"booking_id":None,"message":"Booking timed out."}
        except Exception as e:
            if any(k in str(e).lower() for k in ("timeout","connection","503","502")) and attempt<3:
                await asyncio.sleep(float(attempt)); continue
            return {"success":False,"booking_id":None,"message":str(e)}
    return {"success":False,"booking_id":None,"message":"Failed after retries."}

def cancel_booking(booking_id, reason="Cancelled by caller"):
    creds = get_cal_creds()
    try:
        resp = requests.delete(f"{CAL_BASE}/bookings/{booking_id}/cancel?apiKey={creds['api_key']}", headers={"Content-Type":"application/json"}, json={"reason":reason}, timeout=8)
        resp.raise_for_status()
        return {"success":True,"message":"Cancelled"}
    except Exception as e: return {"success":False,"message":str(e)}
