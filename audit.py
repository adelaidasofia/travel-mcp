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
    # Free text carries document numbers no key can announce; an exception
    # message is free text too.
    return redact_labelled_documents(cleaned)


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
    # passport, and the spellings a caller actually reaches for
    "passport", "passport_number", "passport_no", "passport_num",
    "passport_id", "passport_details", "passport2", "passport_2",
    "second_passport", "second_passport_number", "passport_number_2",
    # generic document
    "document_number", "doc_number", "id_number", "id_no",
    "identity_document", "identity_number",
    # national / tax identifiers, US
    "national_id", "nationalid", "national_id_number", "national_identity_number",
    "passport_number2", "passportnumber2", "passport_no2",
    "ssn", "social_security", "social_security_number", "tax_id", "tin",
    # LatAm national + tax documents. The primary traveler here is a dual
    # Colombian/US national, so these are the expected case, not an exotic one.
    "cedula", "cedula_de_ciudadania", "dni", "nit", "curp", "rfc",
    "pasaporte", "numero_de_pasaporte", "documento", "documento_de_identidad",
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


# A document number written into PROSE or into a FILENAME, where no key names
# it. Detection is label PROXIMITY plus a document-shaped token, not a shape
# regex alone: a bare shape match would eat flight numbers and PNRs. An earlier
# version required an explicit ":" separator and therefore missed both
# "passport stamp AN7734512" and the slugified filename form
# "Passport-number-AN7734512", where every separator has collapsed to a hyphen.
_DOC_LABEL_RE = re.compile(
    r"(?i)\b(passports?|pasaportes?|c[ée]dulas?|dni|nit|curp|rfc|ssn|"
    r"social\s+security|national\s+id|tax\s+id|documentos?|"
    r"(?:travel\s+|identity\s+|government\s+)?document|"
    r"id\s+(?:number|no\.?|card)|identification|identificaci[óo]n|"
    r"reisepass|passeport)\b"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{3,}")

#: Labels that OWN their number. A booking reference and a passport number are
#: indistinguishable by shape, so shape can never separate them -- but the noun
#: immediately in front of the number can. When one of these sits closer to the
#: token than the document label does, the number belongs to the itinerary.
_ITINERARY_LABEL_RE = re.compile(
    r"(?i)\b(booking|confirmation|confirm|record\s+locator|pnr|"
    r"flight|ticket|reserva|localizador|orden|order)\b"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}\S*)?$")

#: A flight number carries at most 4 digits (AA1234); a PNR carries few. Real
#: document numbers carry long digit runs. Thresholding on the RUN rather than
#: the total is what separates "passport ... flight AA1234" from a passport
#: number sitting next to the same word.
_MIN_DIGITS_IN_GROUP = 5
#: A grouped document number (Colombian cédula "1.020.123.456") has short
#: groups but a long total once separators are stripped.
_MIN_DIGITS_TOTAL_GROUPED = 8

#: How far past a document label a number still counts as that label's value.
#: Wide enough for "passport number is X" and for a slugified filename where
#: every separator has collapsed to a hyphen; narrow enough that an unrelated
#: number later in the same paragraph is not swept up.
_LABEL_PROXIMITY = 40


def _looks_like_document_number(token: str) -> bool:
    """A token shaped like a document NUMBER rather than a word, date or code.

    An earlier threshold of "5+ chars with 2+ digits" swept up everything travel
    prose is made of: flight numbers, PNRs, the Schengen "90-day" rule, fee
    ranges like "130-165" and form codes like "DS-11". Splitting on the
    separators first, then asking for a long digit RUN, keeps those while still
    catching AN7734512 and the grouped Colombian form 1.020.123.456.

    ISO dates and datetimes are excluded outright: passport EXPIRY is data this
    system deliberately keeps.
    """
    token = token.strip(".-")
    if not token or _ISO_DATE_RE.match(token):
        return False
    groups = [g for g in re.split(r"[.\-]", token) if g]
    for g in groups:
        digits = sum(c.isdigit() for c in g)
        # An ALPHANUMERIC document number (AN7734512) is unmistakable at 5
        # digits. A PURE number must be long, because short bare numbers are
        # room numbers, quantities and Colombian-peso fees -- "Passport office
        # room 12345" and "Passport fee 150000 COP" are ordinary travel notes,
        # while a real national ID runs to 8+ digits.
        if any(c.isalpha() for c in g):
            if digits >= _MIN_DIGITS_IN_GROUP:
                return True
        elif digits >= _MIN_DIGITS_TOTAL_GROUPED:
            return True
    # Grouped form: short groups, long total, and made only of digit groups
    # (so "fee USD 130-165" -- two groups, 6 digits -- stays below the bar).
    if len(groups) >= 3 and all(g.isdigit() for g in groups):
        return sum(len(g) for g in groups) >= _MIN_DIGITS_TOTAL_GROUPED
    return False


#: How many filler words may sit between a document label and its number.
#: Wide enough for "Cedula de ciudadania: X" and "Passport document number: X";
#: token SHAPE, not this count, is what keeps "Passport office room 12345" and
#: "Passport fee 150000 COP" writable.
_MAX_FILLER_WORDS = 3
_FILLER_RE = re.compile(r"^[\s:=#.\-'’()\[\]]*([A-Za-zÀ-ſ]{1,15})(?=[\s:=#.\-'’()\[\]]|$)")


def _find_document_spans(text: str, *, strict: bool = False) -> list[tuple[int, int]]:
    """Spans of document-number tokens sitting just after a document label.

    Two strictnesses, because the two sinks have opposite failure costs. The
    AUDIT LOG scans a wide window: an over-redaction there costs one degraded
    log line. The VAULT is strict: a false positive raises a hard exception and
    refuses the user's own note, so a number is only a document number when it
    is unmistakably that label's value.
    """
    spans: list[tuple[int, int]] = []
    for label in _DOC_LABEL_RE.finditer(text):
        window = text[label.end():label.end() + _LABEL_PROXIMITY]
        for tok in _TOKEN_RE.finditer(window):
            if not _looks_like_document_number(tok.group(0)):
                continue
            # Whichever label sits CLOSEST to the number owns it.
            gap_text = window[:tok.start()]
            it = list(_ITINERARY_LABEL_RE.finditer(gap_text))
            if it and not _DOC_LABEL_RE.search(gap_text[it[-1].end():]):
                continue
            if strict:
                gap = window[:tok.start()]
                fillers = 0
                while (m := _FILLER_RE.match(gap)):
                    fillers += 1
                    gap = gap[m.end():]
                if fillers > _MAX_FILLER_WORDS or gap.strip(" :=#.-\t\n\r'’()[]"):
                    continue
            spans.append((label.end() + tok.start(), label.end() + tok.end()))
    return spans


def redact_labelled_documents(text: str) -> str:
    """Redact a document number that free text puts next to its own label."""
    spans = _find_document_spans(text)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + _REDACTED + text[end:]
    return text


def contains_labelled_document(text: Any) -> bool:
    """STRICT test used by the vault write boundary (raises a hard exception)."""
    return isinstance(text, str) and bool(_find_document_spans(text, strict=True))


def find_document_violations(obj: Any, _path: str = "", _seen: set[int] | None = None) -> list[str]:
    """Every document-number disclosure anywhere inside a nested structure.

    Walks to arbitrary depth. A shallow check over top-level values only is not
    enough: frontmatter routinely carries a LIST OF DICTS (the been-there ledger
    stores `records: [{...}]`), so the disclosure sits two levels down where a
    top-level scan never looks. Returns human-readable locations, empty when
    clean.
    """
    # A self-referential structure previously recursed until RecursionError,
    # which inside record() was swallowed and dropped the whole audit line.
    _seen = set() if _seen is None else _seen
    if isinstance(obj, (dict, list, tuple, set, frozenset)) or hasattr(obj, "__dict__"):
        if id(obj) in _seen:
            return []
        _seen = _seen | {id(obj)}
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            where = f"{_path}.{k}" if _path else str(k)
            if v is not None and is_document_number_key(k):
                hits.append(f"key {where!r}")
            else:
                hits.extend(find_document_violations(v, where, _seen))
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, v in enumerate(obj):
            hits.extend(find_document_violations(v, f"{_path}[{i}]", _seen))
    elif isinstance(obj, str):
        if _find_document_spans(obj, strict=True):
            hits.append(f"labelled number in {_path or 'text'!r}")
    elif hasattr(obj, "__dict__"):
        hits.extend(find_document_violations(vars(obj), _path, _seen))
    return hits


def _split_camel(text: str) -> str:
    """passportNo -> passport_no, so camelCase cannot smuggle a known key past."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)


def _normalize_key(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    k = _split_camel(key.strip())
    return re.sub(r"[\s\-]+", "_", k).lower()


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


def sanitize_payload(obj: Any, _seen: frozenset[int] = frozenset()) -> Any:
    """Recursively sanitize a dict/list/str payload for the audit io field.

    Two independent passes. Values under an identity-document key are dropped
    outright; every remaining string is scrubbed for credentials. A None stays
    None rather than becoming a redaction marker, so "field not supplied" and
    "field supplied and withheld" stay distinguishable in the audit trail.
    """
    # Cycle guard: a self-referential payload used to recurse until
    # RecursionError, which record()'s broad except swallowed -- dropping the
    # entire audit line, the exact silent failure this function exists to end.
    if isinstance(obj, (dict, list, tuple, set, frozenset)) or hasattr(obj, "__dict__"):
        if id(obj) in _seen:
            return "***CYCLE***"
        _seen = _seen | {id(obj)}
    if isinstance(obj, dict):
        return {
            # A sensitive key is redacted WHOLESALE. Recursing into its value
            # would let {"passport_details": {"number": ...}} survive, because
            # the inner key is innocuous on its own.
            k: (_REDACTED if _is_sensitive_key(k) and v is not None
                else sanitize_payload(v, _seen))
            for k, v in obj.items()
        }
    # tuple and set are NOT dict/list/str. Returning them unrecursed let their
    # contents reach the log unsanitised while json.dumps rendered them as an
    # ordinary array -- on-disk identical to a sanitised list, opposite meaning.
    if isinstance(obj, (list, tuple)):
        return [sanitize_payload(v, _seen) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [sanitize_payload(v, _seen) for v in sorted(obj, key=repr)]
    if isinstance(obj, str):
        return sanitize_error(obj)
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    # Anything else (dataclass, arbitrary object) previously reached json.dumps
    # untouched, raised TypeError, and record()'s broad except DROPPED THE WHOLE
    # AUDIT LINE with no error. Degrade to a sanitised string instead: a
    # less-precise line beats a silently missing one.
    if hasattr(obj, "__dict__"):
        return sanitize_payload(vars(obj), _seen)
    return sanitize_error(repr(obj))


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
            # default=str so an unforeseen type degrades this FIELD rather than
            # dropping the whole line through the except below.
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
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
