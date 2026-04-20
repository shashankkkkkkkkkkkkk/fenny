import re


def normalize_phone_number(phone: str | None) -> str:
    raw = (phone or "").strip()
    if not raw:
        return ""

    lowered = raw.lower()
    if lowered in ("unknown", "demo"):
        return lowered

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw

    if raw.startswith("00") and len(digits) > 2:
        digits = digits[2:]

    # India-specific normalization:
    # +91XXXXXXXXXX, 91XXXXXXXXXX and XXXXXXXXXX are all treated as the same.
    if digits.startswith("91"):
        local = digits[2:]
        if len(local) == 10:
            return f"+91{local}"
    elif len(digits) == 10:
        return f"+91{digits}"

    return f"+{digits}" if not raw.startswith("+") else f"+{digits}"


def is_valid_phone_number(phone: str | None) -> bool:
    normalized = normalize_phone_number(phone)
    if normalized in ("", "unknown", "demo"):
        return False
    if normalized.startswith("+91"):
        return bool(re.fullmatch(r"\+91\d{10}", normalized))
    return bool(re.fullmatch(r"\+[1-9]\d{7,14}", normalized))


def phone_match_variants(phone: str | None) -> list[str]:
    normalized = normalize_phone_number(phone)
    if not normalized:
        return []
    if normalized in ("unknown", "demo"):
        return [normalized]
    variants = {normalized}
    if normalized.startswith("+91") and len(normalized) == 13:
        local = normalized[3:]
        variants.add(f"91{local}")
        variants.add(local)
    return list(variants)


def is_demo_phone(phone: str | None) -> bool:
    return normalize_phone_number(phone) == "demo"
