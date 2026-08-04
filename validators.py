"""Input validators for travel-mcp tools.

INPUT rail (per llm-rails-taxonomy.md): every LLM-calling tool runs its arguments
through these helpers BEFORE building a prompt. Validation failures return an
error_class="validation" payload to the caller and never reach the LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

IATA_RE = re.compile(r"^[A-Z]{3}$")
ICAO_RE = re.compile(r"^[A-Z]{4}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CABIN_VALUES = {"economy", "premium_economy", "business", "first"}
RISK_VALUES = {"low", "medium", "high"}
TRIP_TYPE_VALUES = {"leisure", "business", "holiday", "family", "vacation", "mix"}
ONE_WAY_TOKENS = {"one-way", "oneway", "one_way"}

# ---- itinerary / preference / ledger vocabularies (canonical wire values) ----
# Hyphenated forms are canonical. Underscores and spaces normalize to hyphens so
# `been_done`, `been done` and `been-done` are one value, never three.
SEGMENT_STATUS_VALUES = ("locked", "planned", "candidate")
SEGMENT_SCOPE_VALUES = ("personal", "company")
PLACE_RELATIONSHIP_VALUES = (
    "for-a-person",
    "love-the-place",
    "never-mind-it",
    "been-done",
    "want-to-go",
)
PROVENANCE_VALUES = ("inferred", "confirmed", "asked", "unknown")
ELIGIBILITY_STATE_VALUES = ("verified-eligible", "verified-ineligible", "unchecked")
PREREQUISITE_KIND_VALUES = (
    "visa-in-advance",
    "vaccination",
    "permit",
    "document",
    "booking",
)
WEEKDAY_VALUES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ValidationError(ValueError):
    """Raised on invalid tool input. Maps to error_class='validation' in audit."""


def validate_iata(code: str, *, field: str = "airport") -> str:
    """Accept upper/lowercase 3-letter IATA codes; reject city names."""
    if not isinstance(code, str):
        raise ValidationError(f"{field} must be a string IATA code (e.g. 'BOG', 'JFK')")
    cleaned = code.strip().upper()
    if not IATA_RE.match(cleaned):
        raise ValidationError(
            f"{field}={code!r} is not a valid IATA code. "
            "Use the 3-letter airport code (e.g. 'GRU' for São Paulo-Guarulhos, 'JFK' for New York-Kennedy)."
        )
    return cleaned


def validate_iso_date(value: str, *, field: str = "date") -> str:
    """Accept ISO YYYY-MM-DD strings; reject other formats."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be an ISO date string YYYY-MM-DD")
    cleaned = value.strip()
    if not ISO_DATE_RE.match(cleaned):
        raise ValidationError(
            f"{field}={value!r} is not a valid ISO date. Use YYYY-MM-DD (e.g. '2026-12-15')."
        )
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValidationError(f"{field}={value!r}: {exc}") from exc
    return cleaned


def validate_cabin(value: str | None) -> str | None:
    """Normalize cabin class; None passes through (caller can default later)."""
    if value is None:
        return None
    cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned not in CABIN_VALUES:
        raise ValidationError(
            f"cabin={value!r} not recognized. Use one of: {sorted(CABIN_VALUES)}"
        )
    return cleaned


def validate_risk_tolerance(value: str | None) -> str:
    if value is None:
        return "medium"
    cleaned = value.strip().lower()
    if cleaned not in RISK_VALUES:
        raise ValidationError(
            f"risk_tolerance={value!r} not recognized. Use one of: {sorted(RISK_VALUES)}"
        )
    return cleaned


def validate_trip_type(value: str | None) -> str:
    if value is None:
        return "leisure"
    cleaned = value.strip().lower()
    if cleaned not in TRIP_TYPE_VALUES:
        raise ValidationError(
            f"trip_type={value!r} not recognized. Use one of: {sorted(TRIP_TYPE_VALUES)}"
        )
    return cleaned


def normalize_dates(value: str) -> dict[str, Any]:
    """Parse a dates string into structured shape.

    Accepts:
      - "2026-12-15"             → {"outbound": "2026-12-15", "return": None}
      - "2026-12-15..2026-12-20" → {"outbound": "...", "return": "..."}
      - "2026-12-15 / 2026-12-20"
      - "2026-12-15, one-way"    → {"outbound": "...", "return": None, "one_way": True}
    """
    if not isinstance(value, str):
        raise ValidationError("dates must be a string")
    raw = value.strip()
    if not raw:
        raise ValidationError("dates must not be empty")
    lower = raw.lower()
    is_one_way = any(tok in lower for tok in ONE_WAY_TOKENS)
    cleaned = re.sub(r"[,;]?\s*(one[\s_-]?way)", "", raw, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s*(?:\.\.|/| to | -> )\s*", cleaned)
    parts = [p for p in parts if p]
    if not parts:
        raise ValidationError(f"dates={value!r}: could not parse any date tokens")
    outbound = validate_iso_date(parts[0], field="outbound date")
    return_date: str | None = None
    if len(parts) >= 2 and not is_one_way:
        return_date = validate_iso_date(parts[1], field="return date")
    return {"outbound": outbound, "return": return_date, "one_way": is_one_way or return_date is None}


def validate_slug(value: str) -> str:
    """File-safe slug for trip plans."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("slug must be a non-empty string")
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        raise ValidationError(f"slug={value!r}: contains no file-safe characters")
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip("-")
    return cleaned


def truncate(text: str, limit: int = 600) -> str:
    """Shorten long fields for audit io payloads."""
    if not isinstance(text, str):
        return text
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# =====================================================================
# Itinerary / preference / ledger rails
# =====================================================================

def _normalize_token(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = value.strip().lower().replace("_", "-").replace(" ", "-")
    if not cleaned:
        raise ValidationError(f"{field} must not be empty")
    return cleaned


def _one_of(value: Any, allowed: Sequence[str], *, field: str) -> str:
    cleaned = _normalize_token(value, field=field)
    if cleaned not in allowed:
        raise ValidationError(f"{field}={value!r} not recognized. Use one of: {list(allowed)}")
    return cleaned


def validate_segment_status(value: Any) -> str:
    """locked | planned | candidate."""
    return _one_of(value, SEGMENT_STATUS_VALUES, field="status")


def validate_segment_scope(value: Any) -> str:
    """personal | company. Present from the first migration even while the company lane is empty."""
    return _one_of(value, SEGMENT_SCOPE_VALUES, field="scope")


def validate_provenance(value: Any) -> str:
    """inferred | confirmed | asked | unknown."""
    return _one_of(value, PROVENANCE_VALUES, field="provenance")


def validate_eligibility_state(value: Any) -> str:
    """verified-eligible | verified-ineligible | unchecked."""
    return _one_of(value, ELIGIBILITY_STATE_VALUES, field="state")


def validate_prerequisite_kind(value: Any) -> str:
    """visa-in-advance | vaccination | permit | document | booking."""
    return _one_of(value, PREREQUISITE_KIND_VALUES, field="kind")


def validate_relationships(values: Any, *, field: str = "relationships") -> tuple[str, ...]:
    """A place carries MULTIPLE relationships at once — never exactly one.

    Returns them deduplicated and ordered by the canonical vocabulary so two
    equivalent inputs always compare equal. At least one is required.
    """
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValidationError(f"{field} must be a list of relationship tokens")
    cleaned = {_one_of(v, PLACE_RELATIONSHIP_VALUES, field=field) for v in values}
    if not cleaned:
        raise ValidationError(
            f"{field} must contain at least one of: {list(PLACE_RELATIONSHIP_VALUES)}"
        )
    return tuple(r for r in PLACE_RELATIONSHIP_VALUES if r in cleaned)


def validate_weekdays(values: Any, *, field: str = "days") -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValidationError(f"{field} must be a list of weekday tokens (mon..sun)")
    cleaned = {_one_of(v, WEEKDAY_VALUES, field=field)[:3] for v in values}
    return tuple(d for d in WEEKDAY_VALUES if d in cleaned)


def coerce_iso_date(value: Any, *, field: str = "date") -> str:
    """Accept an ISO string, a date, or a datetime and return 'YYYY-MM-DD'.

    Hand-edited YAML yields real `date` objects for unquoted `2026-10-08`, so
    every load path coerces rather than assuming the writer's quoting survived.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return validate_iso_date(value, field=field)


def validate_date_order(start: Any, end: Any, *, start_field: str = "start",
                        end_field: str = "end") -> tuple[str, str]:
    """Coerce both ends and assert start <= end."""
    s = coerce_iso_date(start, field=start_field)
    e = coerce_iso_date(end, field=end_field)
    if date.fromisoformat(e) < date.fromisoformat(s):
        raise ValidationError(f"{end_field}={e} is before {start_field}={s}")
    return s, e


def validate_hhmm(value: Any, *, field: str = "time") -> str:
    if not isinstance(value, str) or not HHMM_RE.match(value.strip()):
        raise ValidationError(f"{field}={value!r} must be 24-hour HH:MM (e.g. '09:30')")
    return value.strip()


def validate_unit_interval(value: Any, *, field: str) -> float:
    """A 0.0-1.0 dial. Dials are independent of each other — never normalized as a pair."""
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a number between 0 and 1") from exc
    if num != num or num < 0.0 or num > 1.0:  # NaN-safe
        raise ValidationError(f"{field}={value!r} must be between 0.0 and 1.0 inclusive")
    return num


def validate_positive_number(value: Any, *, field: str, maximum: float | None = None) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a number") from exc
    if num != num or num <= 0:
        raise ValidationError(f"{field}={value!r} must be greater than 0")
    if maximum is not None and num > maximum:
        raise ValidationError(f"{field}={value!r} must be <= {maximum}")
    return num


def validate_non_negative_int(value: Any, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a whole number >= 0")
    if float(value) != int(value):
        raise ValidationError(f"{field}={value!r} must be a whole number")
    num = int(value)
    if num < 0:
        raise ValidationError(f"{field}={value!r} must be >= 0")
    if maximum is not None and num > maximum:
        raise ValidationError(f"{field}={value!r} must be <= {maximum}")
    return num


def validate_name(value: Any, *, field: str, limit: int = 120) -> str:
    """Non-empty display string (city, country, place, label)."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValidationError(f"{field} must be <= {limit} characters")
    return cleaned


def validate_key(value: Any, *, field: str = "key") -> str:
    """Stable lookup key: lowercase, hyphen-separated, file-safe."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        raise ValidationError(f"{field}={value!r} contains no usable characters")
    return cleaned[:64].rstrip("-")


def normalize_country(value: Any, *, field: str = "country") -> str:
    """Country label, case-folded for lookup stability.

    Deliberately NOT an ISO-code whitelist: the model must accept any label the
    traveler uses. Case-folding is what makes 'Country A' and 'country a' one row.
    """
    return validate_name(value, field=field)


def country_key(value: Any, *, field: str = "country") -> str:
    return normalize_country(value, field=field).casefold()
