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
