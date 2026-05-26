"""Input validators for travel-mcp tools.

INPUT rail (per llm-rails-taxonomy.md): every LLM-calling tool runs its arguments
through these helpers BEFORE building a prompt. Validation failures return an
error_class="validation" payload to the caller and never reach the LLM.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

IATA_RE = re.compile(r"^[A-Z]{3}$")
ICAO_RE = re.compile(r"^[A-Z]{4}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CABIN_VALUES = {"economy", "premium_economy", "business", "first"}
RISK_VALUES = {"low", "medium", "high"}
TRIP_TYPE_VALUES = {"leisure", "business", "holiday", "family", "vacation", "mix"}
ONE_WAY_TOKENS = {"one-way", "oneway", "one_way"}


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
