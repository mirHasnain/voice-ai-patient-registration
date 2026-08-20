"""Validation and speech normalization for patient records.

Validation runs server-side on every write, from the REST API and the voice
agent alike.

Normalization handles speech-to-text output. Callers say "March third,
nineteen eighty-five", "D-A-V-I-S", "California", "nine oh two one oh".
Handling this in code rather than in the prompt keeps it deterministic and
testable.

Failure messages are phrased as instructions for the voice agent ("ask the
caller to repeat X") because they are returned to the model as tool results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

SEX_VALUES = ["Male", "Female", "Other", "Decline to Answer"]

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "puerto rico": "PR",
    "virgin islands": "VI", "guam": "GU",
}
_STATE_CODES = set(US_STATES.values())

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

REQUIRED_FIELDS = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]

# Returned to the model as tool-result text, so each message is phrased as an
# instruction rather than as an error string. See app/routes/vapi.py.
_REPROMPT = {
    "first_name": "Ask the caller to say and spell their first name.",
    "last_name": "Ask the caller to say and spell their last name.",
    "date_of_birth": "Ask the caller to repeat their date of birth, including the four-digit year.",
    "sex": "Ask the caller for their sex: male, female, other, or decline to answer.",
    "phone_number": "Ask the caller to repeat their 10-digit phone number, including area code, one digit at a time.",
    "address_line_1": "Ask the caller for their street address, including the house number.",
    "city": "Ask the caller which city they live in.",
    "state": "Ask the caller which US state they live in.",
    "zip_code": "Ask the caller to repeat their 5-digit ZIP code one digit at a time.",
    "email": "Ask the caller to repeat their email address slowly.",
    "emergency_contact_phone": "Ask the caller to repeat the emergency contact 10-digit phone number.",
}

_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ' -]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _words_to_digits(value) -> str:
    """"five five five one two three four" -> "5551234"."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(value).lower())
    return "".join(_DIGIT_WORDS.get(tok, tok) for tok in cleaned.split())


def normalize_name(raw):
    """Normalize a name, including the spell-out case.

    A caller correcting the agent says "D-A-V-I-S", which the transcriber
    renders literally. Hyphen-separated single letters are joined; genuine
    hyphenated names such as Smith-Jones have multi-letter parts and are kept.
    """
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw).strip())
    if not text:
        return None

    joined = []
    for word in text.split(" "):
        parts = word.split("-")
        joined.append("".join(parts) if len(parts) > 1 and all(len(p) == 1 for p in parts) else word)
    text = " ".join(joined)

    # Only re-case words carrying no case information of their own, such as
    # "maria" or an all-caps spell-out. Mixed-case input like "McDonald" is
    # left untouched.
    out = []
    for word in text.split(" "):
        uniform = word == word.lower() or word == word.upper()
        base = word.lower() if uniform else word
        out.append(re.sub(r"(^|['-])([a-zà-ÿ])",
                          lambda m: m.group(1) + m.group(2).upper(), base))
    return " ".join(out)


def normalize_phone(raw):
    """Return 10 digits, or None. Tolerates "+1 (555) 123-4567" and spoken digits."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        digits = re.sub(r"\D", "", _words_to_digits(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    if digits[0] in "01":  # US area codes never start with 0 or 1
        return None
    return digits


def normalize_dob(raw):
    """Return YYYY-MM-DD, or None. Accepts numeric, ISO and spoken month forms."""
    if raw is None:
        return None
    text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", str(raw).strip().lower())

    y = m = d = None
    if match := re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text):
        y, m, d = match.groups()
    elif match := re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", text):
        m, d, y = match.groups()
    elif match := re.match(r"^([a-z]+)\s+(\d{1,2}),?\s+(\d{4})$", text):
        month_name, d, y = match.groups()
        m = _MONTHS.get(month_name)
    elif match := re.match(r"^(\d{1,2})\s+(?:of\s+)?([a-z]+),?\s+(\d{4})$", text):
        d, month_name, y = match.groups()
        m = _MONTHS.get(month_name)
    else:
        return None

    if not all((y, m, d)):
        return None
    y, m, d = int(y), int(m), int(d)

    # Two-digit years resolve to the previous century unless that lands in
    # the future.
    if y < 100:
        y += 1900 if y > datetime.now().year % 100 else 2000

    try:
        return date(y, m, d).isoformat()  # rejects 02/31 and month 13
    except ValueError:
        return None


def is_future_date(iso: str) -> bool:
    return date.fromisoformat(iso) > date.today()


def normalize_state(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    upper = re.sub(r"[^A-Z]", "", text.upper())
    if len(upper) == 2 and upper in _STATE_CODES:
        return upper
    key = re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", "", text.lower())).strip()
    return US_STATES.get(key)


def normalize_zip(raw):
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        digits = re.sub(r"\D", "", _words_to_digits(raw))
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    return None


def normalize_email(raw):
    """"john dot smith at gmail dot com" -> "john.smith@gmail.com"."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    text = re.sub(r"\s+at\s+", "@", text)
    text = re.sub(r"\s+dot\s+", ".", text)
    text = re.sub(r"\s+underscore\s+", "_", text)
    text = re.sub(r"\s+(dash|hyphen)\s+", "-", text)
    return re.sub(r"\s+", "", text)


def is_valid_email(text: str) -> bool:
    return bool(_EMAIL_RE.match(text))


def normalize_sex(raw):
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if re.match(r"^(m|male|man|boy)$", text):
        return "Male"
    if re.match(r"^(f|female|woman|girl)$", text):
        return "Female"
    if re.search(r"(decline|prefer not|rather not|no answer|skip)", text):
        return "Decline to Answer"
    if re.search(r"(other|non.?binary|nb)", text):
        return "Other"
    return None


def normalize_language(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    mapping = {"spanish": "Spanish", "espanol": "Spanish", "english": "English"}
    return mapping.get(text.lower(), text[:1].upper() + text[1:].lower())


def normalize_member_id(raw):
    if raw is None:
        return None
    text = re.sub(r"[^A-Z0-9-]", "", str(raw).upper())
    return text or None


def _clean_text(raw, limit):
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw).strip())
    return text[:limit] if text else None


def _name_field(raw):
    name = normalize_name(raw)
    return name if name and len(name) <= 50 and _NAME_RE.match(name) else None


def _dob_field(raw):
    iso = normalize_dob(raw)
    if not iso or is_future_date(iso) or int(iso[:4]) < 1900:
        return None
    return iso


def _email_field(raw):
    email = normalize_email(raw)
    return email if email and is_valid_email(email) and len(email) <= 254 else None


@dataclass
class ValidationResult:
    ok: bool
    value: dict | None = None
    errors: list[dict] | None = None


def validate_patient(payload, *, partial: bool = False) -> ValidationResult:
    """Normalize + validate a patient payload.

    `partial=True` (used by PUT and update_patient) validates only the fields
    that were actually supplied.
    """
    raw = payload if isinstance(payload, dict) else {}
    out: dict = {}
    errors: list[dict] = []

    def provided(key):
        value = raw.get(key)
        return value is not None and str(value).strip() != ""

    def fail(field, message):
        errors.append({"field": field, "message": f"{message} {_REPROMPT.get(field, '')}".strip()})

    def check(field, normalizer, invalid_message):
        if not provided(field):
            if not partial and field in REQUIRED_FIELDS:
                fail(field, f"{field} is required.")
            return
        value = normalizer(raw[field])
        if value is None:
            fail(field, invalid_message)
        else:
            out[field] = value

    check("first_name", _name_field,
          "first_name must be 1-50 alphabetic characters (hyphens and apostrophes allowed).")
    check("last_name", _name_field,
          "last_name must be 1-50 alphabetic characters (hyphens and apostrophes allowed).")
    check("date_of_birth", _dob_field,
          "date_of_birth must be a real date in the past (MM/DD/YYYY).")
    check("sex", normalize_sex, "sex must be Male, Female, Other, or Decline to Answer.")
    check("phone_number", normalize_phone, "phone_number must be a valid 10-digit US number.")
    check("address_line_1", lambda v: _clean_text(v, 200), "address_line_1 is required.")
    check("city", lambda v: _clean_text(v, 100), "city must be 1-100 characters.")
    check("state", normalize_state, "state must be a valid US state.")
    check("zip_code", normalize_zip, "zip_code must be a 5-digit or ZIP+4 US postal code.")

    def optional(field, normalizer, invalid_message=None):
        if not provided(field):
            return
        value = normalizer(raw[field])
        if value is None:
            if invalid_message:
                fail(field, invalid_message)
            return
        out[field] = value

    optional("email", _email_field, "email is not a valid email address.")
    optional("address_line_2", lambda v: _clean_text(v, 100))
    optional("insurance_provider", lambda v: _clean_text(v, 100))
    optional("insurance_member_id", normalize_member_id)
    optional("preferred_language", normalize_language)
    optional("emergency_contact_name", lambda v: (_name_field(v) or "")[:100] or None)
    optional("emergency_contact_phone", normalize_phone,
             "emergency_contact_phone must be a valid 10-digit US number.")

    if errors:
        return ValidationResult(ok=False, errors=errors)
    return ValidationResult(ok=True, value=out)
