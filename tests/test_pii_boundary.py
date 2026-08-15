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
import re

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

    with pytest.raises(RuntimeError), audit.timed("upsert_companion_profile",
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
    import profile

    import audit

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
    if "slug" in p:
        return "test-trip"
    # These are date-shaped and previously got "test", so the call died in
    # validators.py and the injection never reached the write boundary.
    if p in ("window_start", "arrive", "start"):
        return "2026-10-01"
    if p in ("window_end", "depart", "end"):
        return "2026-10-05"
    if "date" in p or "expiry" in p:
        return "2026-10-01"
    if p == "state":
        return "allowed"
    if p == "status":
        return "planned"
    if p in ("origin", "destination_iata", "iata"):
        return "LHR"
    if "country" in p:
        return "Japan"
    if "city" in p or "place" in p:
        return "Tokyo"
    if "int" in annotation:
        return 1
    if "float" in annotation:
        return 1.0
    if "bool" in annotation:
        return False
    if "dict" in annotation:
        return {}
    if "list" in annotation:
        return []
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

    attempted = reached = 0
    for _name, fn, sig in tools:
        # `dict[str, Any]` contains the substring "str", so a naive check
        # injected a plain string where a dict was required and the call died in
        # validation -- counted as a pass while never reaching the guard.
        str_params = [
            n for n, p in sig.parameters.items()
            if re.fullmatch(r"str( \| None)?", str(p.annotation).replace("typing.", ""))
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
            except profile.IdentityDocumentRejected:
                reached += 1
            except Exception:
                pass  # unrelated validation error: does not prove anything

    # Count injections that actually REACHED the guard. Counting bare attempts
    # let 91 of 123 die on unrelated validation and still report success, so the
    # sweep would have stayed green with the guard removed from write_data_doc.
    assert reached >= 12, (
        f"only {reached}/{attempted} injections reached the guard; the sweep is "
        f"not exercising the write boundary"
    )

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
    with pytest.raises(profile.IdentityDocumentRejected):
        server.update_travel_profile_section("1. Identity", LABELLED)

    assert FAKE_PASSPORT not in profile.profile_path().read_text(encoding="utf-8")
    audit_file = travel_vault / "audit.jsonl"
    assert audit_file.exists(), "audit sink never created; the assertion below would not run"
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
    import profile

    import audit

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



# --------------------------------------------------------------------------
# 8. OVER-BLOCKING. A false positive here raises a hard exception and refuses
#    the user's own note, so these matter as much as the containment tests.
#    The earlier fixtures all put identifiers BEFORE the label, which is
#    structurally outside a forward-scanning window -- they could not fail.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Passport check at gate, flight AA1234",        # identifier AFTER the label
    "Passport ready. PNR X7K2QP",
    "Bring passport; booking ref ABC123456",
    "passport + boarding pass, seat 12A, flight UA0987",
    "Japan: passport 90-day validity rule applies",
    "Schengen: passport 180-day rolling window",
    "Renew passport - fee USD 130-165",
    "national id required, see form DS-11",
    "passport appointment 2026-04-12T09:30",
    "Passport office room 12345, Bogota",
    "passport expires 2029-04-12",
    "passport must have 6 months validity",
])
def test_legitimate_travel_prose_is_not_refused(travel_vault, text):
    import profile

    profile.ensure_dirs()
    profile.save_trip("legit-trip", "summary", text)      # must not raise


@pytest.mark.parametrize("slug", [
    "passport-renewal-2026", "passport-run-2027",
    "visa-and-passport-prep-2026", "cedula-renewal-2026", "ssn-paperwork-2026",
])
def test_document_themed_slugs_are_not_refused(travel_vault, slug):
    """A passport-renewal trip is an ordinary trip and must be saveable."""
    import profile

    profile.ensure_dirs()
    profile.save_trip(slug, "summary", "body")           # must not raise


@pytest.mark.parametrize("text", [
    "Passport check at gate, flight AA1234",
    "Japan: passport 90-day validity rule applies",
])
def test_itinerary_identifiers_survive_the_audit_log_after_a_label(travel_vault, text):
    import audit

    assert "REDACTED" not in audit.sanitize_error(text)


def test_grouped_colombian_cedula_is_caught(travel_vault):
    """1.020.123.456 is how a cedula is actually written."""
    import audit

    assert audit.contains_labelled_document("Cedula: 1.020.123.456")


def test_legacy_stored_number_is_stripped_not_repersisted(travel_vault):
    import profile

    profile.ensure_dirs()
    path = profile._companion_path("Legacy Person")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: travel_companion\nname: Legacy Person\n"
        f"passport: {FAKE_PASSPORT}\n---\n\nbody\n", encoding="utf-8")

    result = profile.upsert_companion("Legacy Person", {"ktn": "TT1234567"})

    assert FAKE_PASSPORT not in path.read_text(encoding="utf-8")
    assert "passport" in result["removed_identity_fields"]
    assert "TT1234567" in path.read_text(encoding="utf-8")


def test_cyclic_payload_does_not_crash_or_drop_the_audit_line(travel_vault):
    import audit

    d = {}
    d["self"] = d
    with audit.timed("t_cycle", input_payload={"d": d}) as ctx:
        ctx["output"] = {"ok": True}
    assert "t_cycle" in (travel_vault / "audit.jsonl").read_text(encoding="utf-8")



# --------------------------------------------------------------------------
# 9. Phrasing corpus. Every form below was found by adversarial review writing
#    a real file to disk, or wrongly refused / corrupted. Pinned so the next
#    threshold change cannot silently trade one direction for the other.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Passport document number: AN7734512",     # two filler words
    "Passport ID number: BB1122334",
    "Passport's number: CC5566778",            # possessive
    "Pasaporte numero: DD9988776",
    "Pasaporte n\u00famero: DD9988776",            # accented filler
    "C\u00e9dula de ciudadan\u00eda: 1.020.123.456",     # canonical Colombian form
    "Passport identification: EE4433221",
    "Passport (number) FF7766554",             # parenthesised filler
    "Passport number:\nGG1231234",             # label and value on separate lines
    "Passport\nnumber\nAN7734512",
    "Document number: AN7734512",
    "ID number: AN7734512",
    "Travel document: AN7734512",
    "Cedula: 1.020.123.456",
])
def test_every_known_disclosure_phrasing_is_refused(travel_vault, text):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("phrasing", "summary", text)


@pytest.mark.parametrize("text", [
    "Bring passport; booking ref ABC123456",       # itinerary label owns the number
    "cedula copy for hotel, confirmation ABC123456",
    "Passport ok, confirmation XY123456",
    "passport done, booking 987654",
    "Passport check at gate, flight AA1234",
    "Passport ready. PNR X7K2QP",
    "passport expires 2029-04-12",
    "Japan: passport 90-day validity rule applies",
    "Renew passport - fee USD 130-165",
    "national id required, see form DS-11",
])
def test_legitimate_strings_survive_both_sinks(travel_vault, text):
    """Neither refused at the vault nor corrupted in the log."""
    import profile

    import audit

    profile.ensure_dirs()
    profile.save_trip("legit", "summary", text)          # must not raise
    assert "REDACTED" not in audit.sanitize_error(text)


def test_legacy_number_in_body_is_reported_not_silently_kept(travel_vault):
    import profile

    profile.ensure_dirs()
    path = profile._companion_path("Legacy Body")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: travel_companion\nname: Legacy Body\n---\n\n"
        f"Passport number: {FAKE_PASSPORT}\n", encoding="utf-8")

    result = profile.upsert_companion("Legacy Body", {"ktn": "TT1234567"})

    assert FAKE_PASSPORT not in path.read_text(encoding="utf-8")
    assert "body" in result["removed_identity_fields"], (
        "reported an all-clear while the number was still on disk")


# --------------------------------------------------------------------------
# 10. Label-collision cover. Both defects below were introduced by the fix
#     itself: a word that belongs to two vocabularies at once.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "1. Avianca - loyalty ID: 12345678 - tier: Diamond",
    "Rental company: Hertz - loyalty ID: 55512345",
    "AAdvantage ID 12345678",
    "Bonvoy ID: 87654321",
])
def test_loyalty_ids_stay_writable(travel_vault, text):
    """`ID` is not a document label on its own; PROFILE_TEMPLATE ships eight
    `loyalty ID:` lines and the tool must not refuse its own template."""
    import profile

    import audit

    profile.ensure_dirs()
    profile.update_profile_section("4. Airports & Flights", text)   # must not raise
    assert "REDACTED" not in audit.sanitize_error(text)


@pytest.mark.parametrize("text", [
    "passport reference number: TT7734512",
    "Passport ref: YY7734512",
    "passport reference TT7734512",
    "cedula reference number 1020123456",
    "national id reference AB7734512",
])
def test_reference_does_not_exempt_a_document_number(travel_vault, text):
    """"reference" belongs to both vocabularies: "passport reference number" is
    a standard name for the passport number, so it must not act as an
    itinerary label and exempt the document's own value."""
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("ref-collision", "summary", text)


def test_legacy_number_in_an_untouched_profile_section_is_remediated(travel_vault):
    import profile

    profile.ensure_dirs()
    pp = profile.profile_path()
    pp.write_text(
        "---\ntype: travel_profile\n---\n\n## 1. Identity\n\n"
        f"Passport number: {FAKE_PASSPORT}\n\n## 4. Airports & Flights\n\nold\n",
        encoding="utf-8")

    result = profile.update_profile_section("4. Airports & Flights", "Primary airport: BOG")

    assert FAKE_PASSPORT not in pp.read_text(encoding="utf-8")
    assert "body" in result["removed_identity_fields"]
    assert "Primary airport: BOG" in pp.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 11. Structural disambiguation. `reference`/`ref` and `ID` belong to BOTH
#     vocabularies, so no label set can own them: assigning them to itinerary
#     leaked passport numbers, assigning them to document ate booking refs.
#     They are disambiguated by STRUCTURE, and both directions are pinned here
#     so neither can be traded for the other again.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "passport copy + ref ABC123456",             # conjunction -> two nouns
    "passport and hotel reference ABC123456",
    "passport scan and reference DEF987654",
    "Passport photo + reference GHI555444",
    "passport copy and booking reference ABC123456",
])
def test_reference_after_a_conjunction_is_an_itinerary_value(travel_vault, text):
    import profile

    import audit

    profile.ensure_dirs()
    profile.save_trip("ref-ok", "summary", text)          # must not raise
    assert "REDACTED" not in audit.sanitize_error(text)


@pytest.mark.parametrize("text", [
    "passport reference number: TT7734512",      # continuous noun phrase
    "Passport ref: YY7734512",
    "cedula reference number 1020123456",
    "national id reference AB7734512",
])
def test_reference_without_a_conjunction_names_the_document(travel_vault, text):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("ref-doc", "summary", text)


@pytest.mark.parametrize("text", [
    "ID: M17734512", "Colombian ID N27734512", "ID# P47734512",
    "my ID is O37734512", "ID no. Q57734512", "ID card R67734512",
])
def test_bare_id_with_an_alphanumeric_token_is_a_document(travel_vault, text):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("id-doc", "summary", text)


@pytest.mark.parametrize("text", [
    "AAdvantage ID 12345678", "Bonvoy ID: 87654321",
    "Marriott Bonvoy ID 123456789", "Rental company: Hertz - loyalty ID: 55512345",
])
def test_bare_id_with_a_pure_number_is_a_loyalty_id(travel_vault, text):
    """A loyalty number is pure digits; a document number written after bare
    `ID` is alphanumeric. Shape is what separates them."""
    import profile

    import audit

    profile.ensure_dirs()
    profile.save_trip("id-loyalty", "summary", text)      # must not raise
    assert "REDACTED" not in audit.sanitize_error(text)


# --------------------------------------------------------------------------
# 12. The irreducible band, pinned so its boundary is explicit rather than
#     accidental. `,` and `;` are PUNCTUATION to skip over, not conjunctions:
#     "cedula, ref 1020123456" lists the cedula's OWN reference and must
#     refuse. The cost is one named over-block below. A document number on
#     disk outranks a refused note, so the trade is deliberate, not incidental.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "cedula, ref 1020123456",
    "passport; ref AN7734515",
    "passport & ref AN7734516",
    "Traveler docs, passport, ref AN7734518",
])
def test_punctuation_does_not_exempt_a_document_number(travel_vault, text):
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("punct", "summary", text)


def test_accepted_overblock_comma_before_ref(travel_vault):
    """ACCEPTED COST, pinned so it stays visible instead of being rediscovered.

    "Passport copy, ref ABC123456" is a booking reference and is refused,
    because once the comma is punctuation it is structurally identical to
    "cedula, ref 1020123456". Only the token differs, and ABC123456 vs
    AN7734512 is one digit apart -- not separable. Containment wins the tie.
    """
    import profile

    profile.ensure_dirs()
    with pytest.raises(profile.IdentityDocumentRejected):
        profile.save_trip("accepted-overblock", "summary",
                          "Passport copy, ref ABC123456")


@pytest.mark.parametrize("text", [
    "passport and ref AN7734512",
    "passport with reference AN7734513",
    "pasaporte y ref AN7734514",
])
def test_known_gap_conjunction_before_ref_is_not_caught(travel_vault, text):
    """KNOWN GAP, pinned so it can never be mistaken for coverage.

    A real conjunction makes the phrase genuinely ambiguous: "passport and ref
    X" can mean the passport's reference or a booking reference, and a human
    reader cannot tell either. The rule resolves toward itinerary, so these
    WRITE. Recorded in the commit message; not silently absent from the suite.
    """
    import profile

    profile.ensure_dirs()
    profile.save_trip("known-gap", "summary", text)      # writes, by design
