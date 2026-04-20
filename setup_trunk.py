import asyncio
import os
from dotenv import load_dotenv
from livekit import api

# Load environment variables
load_dotenv(".env")


def _is_placeholder(value: str | None, marker: str) -> bool:
    return not value or marker in value


def _persist_sip_trunk_id(env_path: str, trunk_id: str) -> None:
    """Write SIP_TRUNK_ID to .env so agent.py can read it reliably."""
    try:
        if not os.path.exists(env_path):
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"SIP_TRUNK_ID={trunk_id}\n")
            return

        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("SIP_TRUNK_ID="):
                lines[i] = f"SIP_TRUNK_ID={trunk_id}\n"
                updated = True
                break

        if not updated:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"SIP_TRUNK_ID={trunk_id}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        print(f"Warning: Could not persist SIP_TRUNK_ID to .env: {e}")

async def main():
    env_path = ".env"
    trunk_id = os.getenv("SIP_TRUNK_ID") or os.getenv("OUTBOUND_TRUNK_ID")
    address = os.getenv("VOBIZ_SIP_DOMAIN")
    username = os.getenv("VOBIZ_USERNAME")
    password = os.getenv("VOBIZ_PASSWORD")
    number = os.getenv("VOBIZ_OUTBOUND_NUMBER")
    
    if _is_placeholder(address, "your_sip_domain"):
        print("Error: VOBIZ_SIP_DOMAIN is missing or still placeholder")
        return

    if _is_placeholder(username, "your_username"):
        print("Error: VOBIZ_USERNAME is missing or still placeholder")
        return

    if _is_placeholder(password, "your_password"):
        print("Error: VOBIZ_PASSWORD is missing or still placeholder")
        return

    if _is_placeholder(number, "X"):
        print("Error: VOBIZ_OUTBOUND_NUMBER is missing or still placeholder")
        return

    # Initialize LiveKit API only after env validation passes
    # Credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) are auto-loaded from .env
    lkapi = api.LiveKitAPI()
    sip = lkapi.sip

    try:
        # If trunk id is not configured, auto-discover existing outbound trunk.
        if not trunk_id:
            listed = await sip.list_sip_outbound_trunk(api.ListSIPOutboundTrunkRequest())
            if len(listed.items) == 1:
                trunk_id = listed.items[0].sip_trunk_id
                print(f"Found existing outbound trunk: {trunk_id}")
            elif len(listed.items) > 1:
                print("Error: Multiple outbound trunks found. Set SIP_TRUNK_ID explicitly in .env")
                for t in listed.items:
                    print(f"  - {t.sip_trunk_id} ({t.name})")
                return
            else:
                # Create outbound trunk when none exists.
                created = await sip.create_sip_outbound_trunk(
                    api.CreateSIPOutboundTrunkRequest(
                        trunk=api.SIPOutboundTrunkInfo(
                            name="vobiz-outbound",
                            address=address,
                            auth_username=username,
                            auth_password=password,
                            numbers=[number],
                        )
                    )
                )
                trunk_id = created.sip_trunk_id
                print(f"Created new outbound trunk: {trunk_id}")

        print(f"Updating SIP Trunk: {trunk_id}")
        print(f"  Address: {address}")
        print(f"  Username: {username}")
        print(f"  Numbers: [{number}]")

        # Update the trunk with the correct credentials and settings
        try:
            await sip.update_outbound_trunk_fields(
                trunk_id,
                address=address,
                auth_username=username,
                auth_password=password,
                numbers=[number] if number else [],
            )
        except Exception as e:
            # If the provided trunk id does not exist (404), create a new one.
            if "not_found" in str(e).lower() or "cannot be found" in str(e).lower():
                print(f"Trunk {trunk_id} not found. Creating a new outbound trunk...")
                created = await sip.create_sip_outbound_trunk(
                    api.CreateSIPOutboundTrunkRequest(
                        trunk=api.SIPOutboundTrunkInfo(
                            name="vobiz-outbound",
                            address=address,
                            auth_username=username,
                            auth_password=password,
                            numbers=[number],
                        )
                    )
                )
                trunk_id = created.sip_trunk_id
                print(f"Created replacement outbound trunk: {trunk_id}")
            else:
                raise

        _persist_sip_trunk_id(env_path, trunk_id)
        print("\n✅ SIP Trunk updated successfully!")
        print(f"Saved SIP_TRUNK_ID={trunk_id} in .env")
        print("The 'max auth retry attempts' error should be resolved now.")
        
    except Exception as e:
        print(f"\n❌ Failed to update trunk: {e}")
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(main())
