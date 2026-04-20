import argparse
import asyncio
import os
import random
import json
from urllib.parse import urlparse
from dotenv import load_dotenv
from livekit import api
from phone_utils import normalize_phone_number, is_valid_phone_number

# Load environment variables
load_dotenv(".env")


def normalize_livekit_api_url(raw_url: str) -> str:
    """Normalize LIVEKIT_URL for server-side API usage.

    LiveKit room clients use ws/wss URLs, but the server SDK talks HTTP(S).
    Accept either format and convert to an API-safe URL.
    """
    value = (raw_url or "").strip()
    if value.startswith("wss://"):
        return "https://" + value[len("wss://") :]
    if value.startswith("ws://"):
        return "http://" + value[len("ws://") :]
    return value


def validate_e164_phone(phone_number: str) -> str | None:
    """Return None for valid input, otherwise a user-friendly validation error."""
    normalized = normalize_phone_number(phone_number)
    if not is_valid_phone_number(normalized):
        return (
            "Invalid phone number format. "
            "For India use +91XXXXXXXXXX, 91XXXXXXXXXX, or XXXXXXXXXX (10 digits). "
            "Other countries must be valid E.164."
        )

    # Common PH formatting mistake: +63 followed by 0 (e.g., +6309...)
    if normalized.startswith("+630"):
        return "For Philippines numbers, remove the leading 0 after +63. Example: +639XXXXXXXXX (not +6309...)."

    return None

async def main():
    parser = argparse.ArgumentParser(description="Make an outbound call via LiveKit Agent.")
    parser.add_argument("--to", required=True, help="The phone number to call (e.g., +91...)")
    args = parser.parse_args()

    # 1. Validation
    phone_number = normalize_phone_number(args.to)
    phone_error = validate_e164_phone(phone_number)
    if phone_error:
        print(f"Error: {phone_error}")
        return

    raw_url = os.getenv("LIVEKIT_URL")
    url = normalize_livekit_api_url(raw_url)
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not (url and api_key and api_secret):
        print("Error: LiveKit credentials missing in .env")
        return

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        print("Error: LIVEKIT_URL is invalid. Example: wss://your-project.livekit.cloud")
        return

    # 2. Setup API Client
    lk_api = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)

    # 3. Create a unique room for this call
    # We use a random suffix to ensure room names are unique
    room_name = f"call-{phone_number.replace('+', '')}-{random.randint(1000, 9999)}"

    print(f"Initiating call to {phone_number}...")
    print(f"Session Room: {room_name}")

    dispatch_request = api.CreateAgentDispatchRequest(
        agent_name="outbound-caller",  # Must match agent.py
        room=room_name,
        metadata=json.dumps({"phone_number": phone_number}),
    )

    try:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                dispatch = await lk_api.agent_dispatch.create_dispatch(dispatch_request)
                print("\n✅ Call Dispatched Successfully!")
                print(f"Dispatch ID: {dispatch.id}")
                print("-" * 40)
                print("The agent is now joining the room and will dial the number.")
                print("Check your agent terminal for logs.")
                break
            except Exception as e:
                msg = str(e)
                is_dns_error = "getaddrinfo failed" in msg.lower()
                if attempt < max_attempts and is_dns_error:
                    print(f"\n⚠️ DNS lookup failed (attempt {attempt}/{max_attempts}). Retrying...")
                    await asyncio.sleep(1.5 * attempt)
                    continue

                print(f"\n❌ Error dispatching call: {e}")
                if is_dns_error:
                    print(
                        "Hint: LIVEKIT_URL host could not be resolved. "
                        "Check internet/DNS and verify project URL in LiveKit Cloud settings."
                    )
                break
    finally:
        await lk_api.aclose()

if __name__ == "__main__":
    asyncio.run(main())
