"""4-field observability + sanitize_error for travel-mcp.

Per MCP Build Runbook §"Per-call observability — 4-field schema":
  execution_time_ms : int
  io                : dict {"input": ..., "output": ...}
  token_usage       : dict {"prompt", "completion", "cache_read", "cache_creation"}
  error_class       : str | None

Plus sanitize_error() per url-input-safety.md §"sanitize_error strip patterns":
  strips api keys / tokens / Bearer headers / passwords from any string crossing
  the user-visible / model-visible boundary.

Append-only JSONL at $HOME/.claude/travel-mcp/audit.log.jsonl. One line per call.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(os.environ.get("TRAVEL_MCP_AUDIT_PATH") or
                  os.path.expanduser("~/.claude/travel-mcp/audit.log.jsonl"))

# Patterns scrubbed from any string before it crosses the seam.
# Ordered most-specific → least-specific so longer prefixes shadow shorter ones.
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-***REDACTED***"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"), "sk-proj-***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "ghp_***REDACTED***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_***REDACTED***"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    (re.compile(r"npm_[A-Za-z0-9]{30,}"), "npm_***REDACTED***"),
    (re.compile(r"sk_live_[0-9a-zA-Z]{20,}"), "sk_live_***REDACTED***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=]+"), "Bearer ***REDACTED***"),
    (re.compile(r"(?i)\bAuthorization\s*[:=]\s*\S+"), "Authorization: ***REDACTED***"),
    (re.compile(r"(?i)\bX-Api-Key\s*[:=]\s*\S+"), "X-Api-Key: ***REDACTED***"),
    (re.compile(r"(?i)\bapi[_\-]?key\s*[:=]\s*\S+"), "api_key=***REDACTED***"),
    (re.compile(r"(?i)\btoken\s*[:=]\s*\S+"), "token=***REDACTED***"),
    (re.compile(r"(?i)\bsecret\s*[:=]\s*\S+"), "secret=***REDACTED***"),
    (re.compile(r"(?i)\bpassword\s*[:=]\s*\S+"), "password=***REDACTED***"),
]


def sanitize_error(text: Any) -> str:
    """Strip credentials from any string before emitting."""
    if not isinstance(text, str):
        text = str(text)
    cleaned = text
    for pattern, replacement in _SCRUB_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


# Field names whose VALUE is identity-document PII whatever its shape.
#
# Matched on the KEY, never on the value. A passport number is shape-identical
# to a flight number (AA1234), a PNR, or a booking reference, so a value regex
# would redact legitimate itinerary data on every route call while still
# missing passport formats it did not anticipate. The key is the reliable
# signal; the value never is.
# Government document NUMBERS. These have no legitimate home in either sink:
# nothing in this system reads them, and a stored one is pure liability.
_DOCUMENT_NUMBER_KEYS: frozenset[str] = frozenset({
    "passport", "passport_number", "passport_no", "passportnumber",
    "national_id", "ssn", "tax_id", "id_number",
})

# The audit set is deliberately WIDER than the vault set. An operational log
# gets copied, pasted into tickets and read while debugging, so date of birth
# and Known Traveler Number are withheld there — but both are ordinary booking
# data that belong in the user's own private vault. Two sinks, two threat
# models, so two lists; collapsing them would silently delete the ability to
# store a KTN, which is the whole point of having one.
_SENSITIVE_KEYS: frozenset[str] = _DOCUMENT_NUMBER_KEYS | frozenset({
    "ktn", "known_traveler_number", "redress", "redress_number",
    "date_of_birth", "dob", "birth_date",
})

_REDACTED = "***REDACTED***"


def _normalize_key(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: Any) -> bool:
    """True when a dict key must be withheld from the AUDIT LOG."""
    return _normalize_key(key) in _SENSITIVE_KEYS


def is_document_number_key(key: Any) -> bool:
    """True when a dict key names a government document NUMBER.

    The vault-write boundary uses this narrower test: a document number is
    refused, while date of birth and KTN are allowed through to the user's
    private vault where booking flows legitimately need them.
    """
    return _normalize_key(key) in _DOCUMENT_NUMBER_KEYS


def sanitize_payload(obj: Any) -> Any:
    """Recursively sanitize a dict/list/str payload for the audit io field.

    Two independent passes. Values under an identity-document key are dropped
    outright; every remaining string is scrubbed for credentials. A None stays
    None rather than becoming a redaction marker, so "field not supplied" and
    "field supplied and withheld" stay distinguishable in the audit trail.
    """
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) and v is not None
                else sanitize_payload(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_payload(v) for v in obj]
    if isinstance(obj, str):
        return sanitize_error(obj)
    return obj


def classify_error(exc: BaseException) -> str:
    """Map an exception to a stable error_class for downstream taxonomy.

    Dispatch walks the MRO, not the exact type name. Matching only the leaf name
    meant every new exception type fell through to `upstream_error` — on a
    surface that makes no upstream call — so the taxonomy said "the provider
    broke" when the caller had simply passed a country nobody had verified.
    Subclasses now inherit their base's class automatically; only genuinely new
    categories need a line here.
    """
    names = {c.__name__ for c in type(exc).__mro__}
    # A recorded policy refusal is neither malformed input nor a fault: the
    # caller asked for something the stored data says no to.
    if "EligibilityRefused" in names:
        return "policy_refusal"
    # ValidationError, PrerequisiteCycleError and UnknownPrerequisiteError all
    # derive from ValueError; all three are caller-input errors.
    if names & {"ValidationError", "ValueError"}:
        return "validation"
    if names & {"TimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return "timeout"
    if names & {"PermissionError", "OSError", "FileNotFoundError", "IsADirectoryError"}:
        return "filesystem"
    if "RuntimeError" in names and "auth" in str(exc).lower():
        return "auth"
    if "RuntimeError" in names:
        return "internal_error"
    return "upstream_error"


def _ensure_dir() -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def record(
    tool: str,
    *,
    execution_time_ms: int,
    io: dict[str, Any],
    token_usage: dict[str, int] | None = None,
    error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a JSONL line. Best-effort: never raises into the caller."""
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "tool": tool,
        "execution_time_ms": int(execution_time_ms),
        "io": sanitize_payload(io),
        "token_usage": token_usage or {
            "prompt": 0, "completion": 0, "cache_read": 0, "cache_creation": 0,
        },
        "error_class": error_class,
    }
    if extra:
        payload["extra"] = sanitize_payload(extra)
    try:
        _ensure_dir()
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Audit must never break the tool call. Silent failure here is acceptable.
        pass


@contextmanager
def timed(tool: str, *, input_payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Context manager that records audit line on __exit__.

    Usage:
        with timed("analyze_route", input_payload={...}) as ctx:
            result = ...
            ctx["output"] = result
            ctx["token_usage"] = {...}   # optional
            ctx["extra"] = {...}         # optional
    """
    start = time.perf_counter()
    ctx: dict[str, Any] = {
        "output": None,
        "token_usage": None,
        "extra": None,
        "error_class": None,
    }
    try:
        yield ctx
    except BaseException as exc:
        ctx["error_class"] = classify_error(exc)
        ctx["output"] = {"error": sanitize_error(str(exc))}
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        record(
            tool,
            execution_time_ms=elapsed_ms,
            io={"input": input_payload, "output": ctx["output"]},
            token_usage=ctx["token_usage"],
            error_class=ctx["error_class"],
            extra=ctx["extra"],
        )
        raise
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    record(
        tool,
        execution_time_ms=elapsed_ms,
        io={"input": input_payload, "output": ctx["output"]},
        token_usage=ctx["token_usage"],
        error_class=ctx["error_class"],
        extra=ctx["extra"],
    )
