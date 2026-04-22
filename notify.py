import logging
logger = logging.getLogger("notify")

def notify_booking_confirmed(caller_name, caller_phone, booking_time_iso, booking_id, notes="", tts_voice="", ai_summary=""):
    logger.info(f"[NOTIFY] Booking confirmed | name={caller_name} phone={caller_phone} id={booking_id} time={booking_time_iso}")
    return True

def notify_call_no_booking(caller_name, caller_phone, call_summary="", tts_voice="", ai_summary="", duration_seconds=0):
    logger.info(f"[NOTIFY] Call ended no booking | name={caller_name or 'Unknown'} phone={caller_phone} duration={duration_seconds}s")
    return False

def notify_agent_error(caller_phone, error):
    logger.error(f"[NOTIFY] Agent error | phone={caller_phone} error={error}")
    return False
