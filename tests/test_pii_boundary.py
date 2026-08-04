"""Identity-document containment (MYC-3671).

Regression cover for a leak with three surfaces: `upsert_companion_profile`
accepted a raw passport number, wrote it to vault frontmatter, and — because
the audit scrubber carried credential patterns only — recorded it verbatim in
audit.log.jsonl as well. The scrubber was reached and did recurse; it simply
had no rule for the field.

These tests are negative controls first. Each asserts the guard FIRES on a
realistic payload, not merely that it stays quiet on clean data, because a
guard that never fires and a guard that cannot fire look identical in a green
suite. The over-blocking direction is covered too: a scrubber that ate flight
numbers or refused a KTN would pass a naive "is the passport gone?" check while
destroying the tool.
"""

from __future__ import annotations

import json

import pytest

# A shape no real scrub pattern would catch by accident, so its survival in any
# sink is unambiguous evidence rather than a coincidence of formatting.
FAKE_PASSPORT = "AN7734512"
FAKE_SSN = "555443333"


# --------------------------------------------------------------------------
# 1. The audit scrubber fires on the exact payload shape server.py sends.
# --------------------------------------------------------------------------

def test_audit_redacts_passport_nested_in_fields(travel_vault):
    import audit

    payload = {"name": "Companion", "fields": {"passport": FAKE_PASSPORT}}
    cleaned = audit.sanitize_payload(payload)

    assert cleaned["fields"]["passport"] == "***REDACTED***"
    assert FAKE_PASSPORT not in json.dumps(cleaned)


def test_audit_redaction_survives_key_spelling_variants(travel_vault):
    import audit

    for key in ("passport_number", "Passport-Number", "PASSPORT NO", "national_id"):
        cleaned = audit.sanitize_payload({key: FAKE_PASSPORT})
        assert cleaned[key] == "***REDACTED***", f"{key!r} leaked"


# --------------------------------------------------------------------------
# 2. End to end: nothing reaches the file on disk.
#    Asserts against raw bytes, not the parsed object — a redaction that only
#    holds in the in-memory copy is not a redaction.
# --------------------------------------------------------------------------

def test_passport_never_written_to_audit_file(travel_vault):
    import audit

    with audit.timed("upsert_companion_profile",
                     input_payload={"name": "X",
                                    "fields": {"passport": FAKE_PASSPORT}}) as ctx:
        ctx["output"] = {"ok": True}

    raw = (travel_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert FAKE_PASSPORT not in raw
    assert "***REDACTED***" in raw


def test_passport_never_written_on_the_error_path(travel_vault):
    """The failure path records its own audit line and must scrub identically."""
    import audit

    with pytest.raises(RuntimeError):
        with audit.timed("upsert_companion_profile",
                         input_payload={"fields": {"passport": FAKE_PASSPORT}}):
            raise RuntimeError("boom")

    raw = (travel_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert FAKE_PASSPORT not in raw


# --------------------------------------------------------------------------
# 3. The vault write boundary refuses document numbers outright.
# --------------------------------------------------------------------------

def test_vault_write_refuses_passport_number(travel_vault):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected) as exc:
        profile.upsert_companion("Test Person", {"passport": FAKE_PASSPORT})
    assert "passport" in str(exc.value)


def test_vault_write_refuses_ssn(travel_vault):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.upsert_companion("Test Person", {"ssn": FAKE_SSN})


def test_refused_write_leaves_no_file_behind(travel_vault):
    """A rejection must not half-write the profile before raising."""
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.upsert_companion("Ghost", {"passport": FAKE_PASSPORT})

    stray = list(travel_vault.rglob("Ghost*"))
    assert stray == [], f"partial write left: {stray}"


# --------------------------------------------------------------------------
# 4. The guard must not over-block. These fail if the two key lists are ever
#    collapsed into one — the exact defect caught during this build.
# --------------------------------------------------------------------------

def test_vault_still_accepts_dob_and_ktn(travel_vault):
    import profile

    profile.ensure_dirs()
    result = profile.upsert_companion(
        "Real Companion",
        {"date_of_birth": "1990-04-12", "ktn": "TT1234567", "passport_country": "US"},
    )
    # Resolved through the module rather than hand-built, so the test cannot
    # drift from the real filename convention.
    written = profile._companion_path("Real Companion").read_text(encoding="utf-8")
    assert "1990-04-12" in written
    assert "TT1234567" in written
    assert result["name"] == "Real Companion"


def test_audit_still_withholds_dob_and_ktn(travel_vault):
    """Allowed in the private vault, withheld from the operational log."""
    import audit

    cleaned = audit.sanitize_payload(
        {"date_of_birth": "1990-04-12", "ktn": "TT1234567"}
    )
    assert cleaned["date_of_birth"] == "***REDACTED***"
    assert cleaned["ktn"] == "***REDACTED***"


def test_dual_passport_country_and_expiry_are_preserved(travel_vault):
    """The replacement model must survive both sinks intact, or the fix has
    removed a capability instead of relocating it."""
    import audit
    import profile

    fields = {
        "passport_country": "CO",
        "passport_expiry": "2029-04-12",
        "second_passport_country": "US",
        "second_passport_expiry": "2031-08-01",
    }
    profile.ensure_dirs()
    profile.upsert_companion("Dual National", fields)
    written = profile._companion_path("Dual National").read_text(encoding="utf-8")
    assert "CO" in written and "US" in written and "2029-04-12" in written

    cleaned = audit.sanitize_payload({"fields": fields})
    assert cleaned["fields"]["passport_country"] == "CO"
    assert cleaned["fields"]["second_passport_country"] == "US"


# --------------------------------------------------------------------------
# 5. No false positives. A value-shaped passport regex would have eaten these,
#    silently corrupting every route call in the audit trail.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("flight_number", "AA1234"),
    ("pnr", "X7K2QP"),
    ("booking_reference", "AN7734512"),   # identical shape, innocent field
    ("origin", "LHR"),
    ("confirmation", "ABC123456"),
])
def test_itinerary_identifiers_are_not_redacted(travel_vault, field, value):
    import audit

    cleaned = audit.sanitize_payload({field: value})
    assert cleaned[field] == value, f"{field} was corrupted by the scrubber"


def test_none_stays_none_rather_than_becoming_a_marker(travel_vault):
    """"Not supplied" and "supplied and withheld" must stay distinguishable."""
    import audit

    cleaned = audit.sanitize_payload({"passport": None, "ktn": None})
    assert cleaned["passport"] is None
    assert cleaned["ktn"] is None


def test_credential_scrubbing_still_works(travel_vault):
    """The original credential patterns must survive the key-based addition."""
    import audit

    cleaned = audit.sanitize_payload({"note": "Bearer abc123def456ghi789"})
    assert "abc123def456ghi789" not in cleaned["note"]


# --------------------------------------------------------------------------
# 6. The removed parameter must not come back.
# --------------------------------------------------------------------------

def test_tool_signature_exposes_no_raw_passport_parameter(travel_vault):
    import inspect

    import server

    sig = inspect.signature(server.upsert_companion_profile)
    assert "passport" not in sig.parameters, "raw passport parameter reintroduced"
    assert "passport_country" in sig.parameters
    assert "second_passport_country" in sig.parameters


# --------------------------------------------------------------------------
# 7. END-TO-END TOOL SURFACE SWEEP.
#
# The first version of these tests had rigorous depth and no breadth: it
# exercised profile.upsert_companion directly and never invoked a single MCP
# tool. Eleven leaking tool paths shipped past a green suite because of it.
#
# This sweep drives EVERY registered tool rather than a hand-picked list, so a
# write path added later is covered without anyone remembering to add it here.
# A tool that rejects the payload is a pass; a tool that raises on unrelated
# validation is a pass; the only failure is the number reaching a sink.
# --------------------------------------------------------------------------

LABELLED = f"Passport number: {FAKE_PASSPORT}"


def _plausible_arg(param: str, annotation: str) -> object:
    """A value shaped well enough to get past input validation."""
    p = param.lower()
    if "slug" in p:                       return "test-trip"
    if "date" in p or "expiry" in p:      return "2026-10-01"
    if p in ("origin", "destination_iata", "iata"): return "LHR"
    if "country" in p:                    return "Japan"
    if "city" in p or "place" in p:       return "Tokyo"
    if "int" in annotation:               return 1
    if "float" in annotation:             return 1.0
    if "bool" in annotation:              return False
    if "dict" in annotation:              return {}
    if "list" in annotation:              return []
    return "test"


def _collect_tools():
    import inspect
    import server
    out = []
    for name in dir(server):
        fn = getattr(server, name)
        if not callable(fn) or name.startswith("_"):
            continue
        if getattr(fn, "__module__", None) != "server":
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        if not sig.parameters:
            continue
        out.append((name, fn, sig))
    return out


def test_no_tool_can_land_a_document_number_in_either_sink(travel_vault):
    """Drive every tool, injecting a labelled passport into each string param."""
    import profile

    profile.ensure_dirs()
    tools = _collect_tools()
    assert len(tools) >= 20, f"tool sweep collected only {len(tools)} tools"

    attempted = 0
    for _name, fn, sig in tools:
        str_params = [
            n for n, p in sig.parameters.items()
            if "str" in str(p.annotation) or p.annotation is str
        ]
        for target in str_params:
            kwargs = {}
            for n, p in sig.parameters.items():
                if n == target:
                    kwargs[n] = LABELLED
                elif p.default is not p.empty:
                    continue
                else:
                    kwargs[n] = _plausible_arg(n, str(p.annotation))
            attempted += 1
            try:
                fn(**kwargs)
            except Exception:
                pass  # refusal or unrelated validation error: both are fine

    assert attempted > 40, f"sweep only attempted {attempted} injections"

    # The only thing that matters: did the number reach a sink?
    leaked_files = [
        f for f in travel_vault.rglob("*")
        if f.is_file() and FAKE_PASSPORT in f.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked_files == [], f"document number reached the vault: {leaked_files}"


def test_the_tool_the_model_is_told_to_use_refuses_a_passport(travel_vault):
    """update_travel_profile_section is where the docstring points the model."""
    import profile
    import server

    profile.ensure_dirs()
    with pytest.raises(Exception):
        server.update_travel_profile_section("1. Identity", LABELLED)

    assert FAKE_PASSPORT not in profile.profile_path().read_text(encoding="utf-8")
    audit_file = travel_vault / "audit.jsonl"
    if audit_file.exists():
        assert FAKE_PASSPORT not in audit_file.read_text(encoding="utf-8")


def test_companion_body_arm_is_guarded_not_just_fields(travel_vault):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.upsert_companion("Body Leak", {}, body=LABELLED)


@pytest.mark.parametrize("key", [
    "second_passport_number", "passport_number_2", "passportNo", "passportNum",
    "passport_num", "passport_id", "passport_details", "document_number",
    "national_id_number", "social_security_number", "cedula", "dni", "nit",
    "curp", "rfc", "pasaporte",
])
def test_document_key_spellings_are_covered_in_both_boundaries(travel_vault, key):
    """Every spelling a caller could plausibly reach for, incl. LatAm documents."""
    import audit
    import profile

    assert audit.sanitize_payload({key: FAKE_PASSPORT})[key] == "***REDACTED***"
    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.upsert_companion("Spelling", {key: FAKE_PASSPORT})


@pytest.mark.parametrize("payload", [
    {"travelers": ({"passport": FAKE_PASSPORT},)},          # tuple
    {"travelers": [({"passport": FAKE_PASSPORT},)]},        # tuple in list
    {"passport_details": {"number": FAKE_PASSPORT}},        # dict under sensitive key
])
def test_container_types_cannot_smuggle_a_number_into_the_log(travel_vault, payload):
    import audit

    with audit.timed("t", input_payload=payload) as ctx:
        ctx["output"] = {"ok": True}
    raw = (travel_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert FAKE_PASSPORT not in raw


def test_unserializable_payload_still_writes_an_audit_line(travel_vault):
    """A dropped line used to be indistinguishable from a call that never ran."""
    import audit

    class Segment:
        def __init__(self): self.city, self.passport = "Tokyo", FAKE_PASSPORT

    with audit.timed("t_obj", input_payload={"segment": Segment()}) as ctx:
        ctx["output"] = {"ok": True}

    raw = (travel_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert "t_obj" in raw, "audit line vanished silently"
    assert FAKE_PASSPORT not in raw


def test_labelled_prose_redacted_but_flight_numbers_survive(travel_vault):
    import audit

    cleaned = audit.sanitize_payload(
        {"note": f"Booked AA1234, PNR X7K2QP. Passport number: {FAKE_PASSPORT}"}
    )
    assert FAKE_PASSPORT not in cleaned["note"]
    assert "AA1234" in cleaned["note"], "flight number was corrupted"
    assert "X7K2QP" in cleaned["note"], "PNR was corrupted"
