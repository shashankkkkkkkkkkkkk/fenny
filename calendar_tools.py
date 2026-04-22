import os
import logging
import requests
import httpx
import asyncio
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("calendar-tools")

CAL_BASE = "https://api.cal.com/v1"
IST = timezone(timedelta(hours=5, minutes=30))
BOOKING_START_HOUR_IST = 9
BOOKING_END_HOUR_IST = 21


def _format_ampm(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _to_ist_datetime(value: str | datetime) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def is_within_booking_hours_ist(value: str | datetime) -> bool:
    dt_ist = _to_ist_datetime(value)
    if not dt_ist:
        return False
    start_minutes = BOOKING_START_HOUR_IST * 60
    end_minutes = BOOKING_END_HOUR_IST * 60
    appt_minutes = dt_ist.hour * 60 + dt_ist.minute
    return start_minutes <= appt_minutes < end_minutes


def get_cal_creds() -> dict:
    raw_event_id = str(os.environ.get("CAL_EVENT_TYPE_ID", "0") or "0").strip()
    event_id = 0
    if raw_event_id:
        try:
            event_id = int(raw_event_id)
        except ValueError:
            logger.error(
                f"[CAL] Invalid CAL_EVENT_TYPE_ID='{raw_event_id}'. "
                "It must be a numeric Cal.com event type ID."
            )
    return {
        "api_key":  os.environ.get("CAL_API_KEY", ""),
        "event_id": event_id,
    }


def get_calendar_setup_error() -> str:
    gcal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")
    if gcal_id and os.path.exists(gcal_creds):
        return ""

    creds = get_cal_creds()
    if not creds["api_key"]:
        return "Calendar setup missing: CAL_API_KEY is not configured."
    if not creds["event_id"]:
        return "Calendar setup missing: CAL_EVENT_TYPE_ID is missing or invalid."
    return ""


# ─── Cal.com: Get available slots ─────────────────────────────────────────────

def get_available_slots(date_str: str) -> list:
    """
    Fetch open slots for a given date from Cal.com OR Google Calendar,
    depending on which is configured.
    date_str: "YYYY-MM-DD"
    """
    setup_error = get_calendar_setup_error()
    if setup_error:
        logger.error(f"[CAL] {setup_error}")
        return []

    # Try Google Calendar first if configured (#36)
    gcal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")
    if gcal_id and os.path.exists(gcal_creds):
        try:
            return _get_slots_gcal(date_str, gcal_id, gcal_creds)
        except Exception as e:
            logger.warning(f"[GCAL] Falling back to Cal.com: {e}")

    # Default: Cal.com
    return _get_slots_calcom(date_str)


def _get_slots_calcom(date_str: str) -> list:
    creds = get_cal_creds()
    try:
        resp = requests.get(
            f"{CAL_BASE}/slots",
            headers={"Content-Type": "application/json"},
            params={
                "apiKey":      creds["api_key"],
                "eventTypeId": creds["event_id"],
                "startTime":   f"{date_str}T00:00:00.000Z",
                "endTime":     f"{date_str}T23:59:59.000Z",
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw_slots = resp.json().get("data", {}).get("slots", {}).get(date_str, [])
        slots = []
        for s in raw_slots:
            dt_ist = _to_ist_datetime(s["time"])
            if not dt_ist or not is_within_booking_hours_ist(dt_ist):
                continue
            slots.append({"time": s["time"], "label": _format_ampm(dt_ist)})
        logger.info(f"[CAL] {len(slots)} slots for {date_str}")
        return slots
    except Exception as e:
        logger.error(f"[CAL] get_available_slots error: {e}")
        return []


def _get_slots_gcal(date_str: str, calendar_id: str, creds_file: str) -> list:
    """
    Fetch busy slots from Google Calendar and compute free windows (#36).
    Requires: google-api-python-client, google-auth
    """
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    service = build("calendar", "v3", credentials=creds)

    start = f"{date_str}T00:00:00+05:30"
    end   = f"{date_str}T23:59:59+05:30"

    result = service.freebusy().query(body={
        "timeMin": start,
        "timeMax": end,
        "items":   [{"id": calendar_id}],
    }).execute()

    busy_slots = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

    # Generate free 30-min slots between 09:00 and 21:00 IST
    import pytz
    ist = pytz.timezone("Asia/Kolkata")
    day_start = ist.localize(datetime.strptime(f"{date_str} 09:00", "%Y-%m-%d %H:%M"))
    day_end   = ist.localize(datetime.strptime(f"{date_str} 21:00", "%Y-%m-%d %H:%M"))

    busy_ranges = []
    for b in busy_slots:
        bs = datetime.fromisoformat(b["start"]).astimezone(ist)
        be = datetime.fromisoformat(b["end"]).astimezone(ist)
        busy_ranges.append((bs, be))

    free_slots = []
    slot = day_start
    while slot < day_end:
        slot_end = slot + timedelta(minutes=30)
        is_busy = any(bs <= slot < be for bs, be in busy_ranges)
        if not is_busy:
            free_slots.append({
                "time":  slot.isoformat(),
                "label": _format_ampm(slot),
            })
        slot = slot_end

    logger.info(f"[GCAL] {len(free_slots)} free slots for {date_str}")
    return free_slots


# ─── Create a booking ──────────────────────────────────────────────────────────

def create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """Synchronous wrapper — calls async_create_booking."""
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(
            async_create_booking(start_time, caller_name, caller_phone, notes)
        )
    except RuntimeError:
        return asyncio.run(async_create_booking(start_time, caller_name, caller_phone, notes))


async def async_create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """
    Book a slot — uses Google Calendar if configured, else Cal.com v2.
    start_time: ISO 8601 with IST offset e.g. "2026-02-24T10:00:00+05:30"
    Returns: {"success": bool, "booking_id": str|None, "message": str}
    """
    gcal_id    = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")

    if not is_within_booking_hours_ist(start_time):
        return {
            "success": False,
            "booking_id": None,
            "message": "Appointments are available only between 9:00 AM and 9:00 PM IST.",
        }

    if gcal_id and os.path.exists(gcal_creds):
        return await _create_booking_gcal(start_time, caller_name, caller_phone, notes, gcal_id, gcal_creds)

    return await _create_booking_calcom(start_time, caller_name, caller_phone, notes)


async def _create_booking_calcom(
    start_time: str, caller_name: str, caller_phone: str, notes: str
) -> dict:
    creds = get_cal_creds()
    if not creds["api_key"]:
        return {"success": False, "booking_id": None, "message": "CAL_API_KEY is missing."}
    if not creds["event_id"]:
        return {"success": False, "booking_id": None, "message": "CAL_EVENT_TYPE_ID is missing or invalid."}

    payload = {
        "eventTypeId": creds["event_id"],
        "start": start_time,
        "attendee": {
            "name":        caller_name,
            "email":       f"{caller_phone.replace('+','').replace(' ','')}@voiceagent.placeholder",
            "phoneNumber": caller_phone,
            "timeZone":    "Asia/Kolkata",
            "language":    "en",
        },
        "bookingFieldsResponses": {
            "notes": notes or f"Booked via AI voice agent. Phone: {caller_phone}",
        },
    }
    headers = {
        "Authorization": f"Bearer {creds['api_key']}",
        "cal-api-version": "2024-08-13",
        "Content-Type": "application/json",
    }
    retries = 3
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.cal.com/v2/bookings",
                    headers=headers,
                    json=payload,
                )
            if resp.status_code in (200, 201):
                uid = resp.json().get("data", {}).get("uid", "unknown")
                logger.info(f"[CAL] Booking created: uid={uid}")
                return {"success": True, "booking_id": uid, "message": "Booking confirmed"}
            if resp.status_code == 409 and "already_signed_up_for_this_booking_error" in resp.text:
                logger.info("[CAL] Booking already exists for this caller and slot; treating as confirmed.")
                return {
                    "success": True,
                    "booking_id": "already-signed-up",
                    "message": "Booking already exists for this caller and time.",
                }
            logger.error(f"[CAL] Booking failed {resp.status_code}: {resp.text}")
            return {"success": False, "booking_id": None, "message": resp.text}
        except httpx.TimeoutException:
            if attempt < retries:
                logger.warning(f"[CAL] Booking request timed out (attempt {attempt}/{retries}), retrying...")
                await asyncio.sleep(float(attempt))
                continue
            return {"success": False, "booking_id": None, "message": "Booking timed out."}
        except Exception as e:
            err = str(e)
            transient = any(k in err.lower() for k in ("timeout", "tempor", "connection", "network", "503", "502", "504"))
            if transient and attempt < retries:
                logger.warning(f"[CAL] Transient booking error (attempt {attempt}/{retries}): {e}")
                await asyncio.sleep(float(attempt))
                continue
            logger.error(f"[CAL] Booking error: {e}")
            return {"success": False, "booking_id": None, "message": err}
    return {"success": False, "booking_id": None, "message": "Booking failed after retries."}


async def _create_booking_gcal(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str,
    calendar_id: str,
    creds_file: str,
) -> dict:
    """Create a Google Calendar event (#36)."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        from datetime import timedelta

        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=creds)

        dt_start = datetime.fromisoformat(start_time)
        dt_end   = dt_start + timedelta(minutes=30)

        event = {
            "summary":     f"Appointment — {caller_name}",
            "description": f"Phone: {caller_phone}\nNotes: {notes}\nBooked via RapidX AI Voice Agent",
            "start":       {"dateTime": dt_start.isoformat(), "timeZone": "Asia/Kolkata"},
            "end":         {"dateTime": dt_end.isoformat(),   "timeZone": "Asia/Kolkata"},
            "attendees":   [{"displayName": caller_name, "comment": caller_phone}],
        }

        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        event_id = created.get("id", "unknown")
        logger.info(f"[GCAL] Event created: id={event_id}")
        return {"success": True, "booking_id": event_id, "message": "Google Calendar event created"}
    except Exception as e:
        logger.error(f"[GCAL] Create booking failed: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


# ─── Cancel a booking ──────────────────────────────────────────────────────────

def cancel_booking(booking_id: str, reason: str = "Cancelled by caller") -> dict:
    """Cancel a Cal.com booking by UID."""
    creds = get_cal_creds()
    try:
        resp = requests.delete(
            f"{CAL_BASE}/bookings/{booking_id}/cancel?apiKey={creds['api_key']}",
            headers={"Content-Type": "application/json"},
            json={"reason": reason},
            timeout=8,
        )
        resp.raise_for_status()
        logger.info(f"[CAL] Booking cancelled: {booking_id}")
        return {"success": True, "message": "Cancelled successfully"}
    except Exception as e:
        logger.error(f"[CAL] cancel_booking error: {e}")
        return {"success": False, "message": str(e)}
