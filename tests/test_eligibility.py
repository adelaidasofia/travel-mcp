"""Entry eligibility: three states, and the refusal that keeps unchecked out of proposals."""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _store(eligibility):
    return (eligibility.EligibilityStore()
            .set_state("Country A", "verified-eligible", source="official portal",
                       as_of="2026-08-01")
            .set_state("Country B", "verified-ineligible", source="official portal",
                       as_of="2026-08-01"))


# =====================================================================
# REFUSAL — a router may only propose verified-eligible
# =====================================================================

def test_unchecked_country_is_refused_for_proposal(travel_vault):
    """NEGATIVE CONTROL: `unchecked` is not a soft yes."""
    import eligibility
    store = _store(eligibility)
    ok, reason, record = store.check_proposable("Country Z")
    assert ok is False
    assert reason == "unchecked"
    assert record.state == "unchecked"
    with pytest.raises(eligibility.EligibilityRefused) as exc:
        store.assert_proposable("Country Z")
    assert exc.value.reason == "unchecked"


def test_verified_ineligible_is_refused(travel_vault):
    import eligibility
    with pytest.raises(eligibility.EligibilityRefused) as exc:
        _store(eligibility).assert_proposable("Country B")
    assert exc.value.state == "verified-ineligible"


def test_verified_eligible_is_the_only_proposable_state(travel_vault):
    import eligibility
    store = _store(eligibility)
    record = store.assert_proposable("Country A")
    assert record.state == "verified-eligible"
    assert record.is_verified_eligible is True


def test_filter_splits_candidates_and_names_every_refusal_reason(travel_vault):
    import eligibility
    result = _store(eligibility).filter_proposable(["Country A", "Country B", "Country Z"])
    assert [r["country"] for r in result["proposable"]] == ["Country A"]
    assert result["proposable_count"] == 1
    assert {r["country"]: r["reason"] for r in result["refused"]} == {
        "Country B": "verified-ineligible",
        "Country Z": "unchecked",
    }


def test_stale_verification_stops_being_proposable(travel_vault):
    """The as-of timestamp is load-bearing, not decoration."""
    import eligibility
    store = _store(eligibility)
    assert store.check_proposable("Country A", now="2026-08-20", max_age_days=30)[0] is True
    ok, reason, _ = store.check_proposable("Country A", now="2027-08-01", max_age_days=30)
    assert ok is False
    assert reason == "stale-verification"


def test_an_as_of_in_the_future_is_refused_not_treated_as_fresh(travel_vault):
    """NEGATIVE CONTROL: a typo'd future date must not read as permanently fresh.

    `age_days` goes NEGATIVE for a future as_of, and `age > max_age_days` can
    never fire on a negative number — so without an explicit check this record
    would be proposable forever, which is the exact lie the module prevents.
    """
    import eligibility
    store = eligibility.EligibilityStore((
        eligibility.EligibilityRecord.build(
            "Country A", "verified-eligible", source="portal", as_of="2030-01-01",
        ),
    ))
    assert store.get("Country A").age_days(now="2026-08-03") < 0
    ok, reason, _ = store.check_proposable("Country A", now="2026-08-03", max_age_days=30)
    assert (ok, reason) == (False, "future-verification")
    # Refused even with no freshness ceiling asked for: it never happened.
    assert store.check_proposable("Country A", now="2026-08-03")[:2] == (False, "future-verification")
    with pytest.raises(eligibility.EligibilityRefused):
        store.assert_proposable("Country A", now="2026-08-03")


def test_set_state_refuses_a_future_as_of_at_the_write_boundary(travel_vault):
    """The typo is caught where it can still be corrected."""
    import eligibility
    import validators as V
    future = (date.today() + timedelta(days=400)).isoformat()
    with pytest.raises(V.ValidationError) as exc:
        eligibility.EligibilityStore().set_state(
            "Country A", "verified-eligible", source="portal", as_of=future,
        )
    assert future in str(exc.value)
    # POSITIVE CONTROL: today and any past date still write.
    today = date.today().isoformat()
    store = eligibility.EligibilityStore().set_state(
        "Country A", "verified-eligible", source="portal", as_of=today,
    )
    assert store.get("Country A").as_of == today


# =====================================================================
# The gate is a CHOKEPOINT, not an opt-in helper
# =====================================================================

def test_guard_raises_on_the_first_country_that_may_not_be_proposed(travel_vault):
    import eligibility
    store = _store(eligibility)
    with pytest.raises(eligibility.EligibilityRefused) as exc:
        eligibility.guard_proposed_countries(
            ["Country A", "Country B", "Country Z"], store=store,
        )
    assert exc.value.state == "verified-ineligible"
    with pytest.raises(eligibility.EligibilityRefused) as unchecked:
        eligibility.guard_proposed_countries(["Country Z"], store=store)
    assert unchecked.value.reason == "unchecked"


def test_guard_passes_verified_eligible_and_no_ops_on_an_empty_list(travel_vault):
    """POSITIVE CONTROL — the gate must let real proposals through."""
    import eligibility
    store = _store(eligibility)
    records = eligibility.guard_proposed_countries(["Country A"], store=store)
    assert [r.state for r in records] == ["verified-eligible"]
    assert eligibility.guard_proposed_countries([], store=store) == ()
    assert eligibility.guard_proposed_countries(None, store=store) == ()


def test_guard_reads_the_saved_store_when_none_is_passed(travel_vault):
    """No store argument means the gate loads from disk, so a caller cannot
    accidentally check against an empty in-memory one."""
    import eligibility
    eligibility.save(_store(eligibility))
    assert eligibility.guard_proposed_countries("Country A")[0].state == "verified-eligible"
    with pytest.raises(eligibility.EligibilityRefused):
        eligibility.guard_proposed_countries("Country B")


# =====================================================================
# DISPLAY — the UI sees everything, labelled
# =====================================================================

def test_display_renders_unchecked_rather_than_hiding_it(travel_vault):
    """Hiding an unknown would make it look like a no."""
    import eligibility
    rows = _store(eligibility).display(["Country A", "Country Z"])
    assert {r["country"]: r["state"] for r in rows} == {
        "Country A": "verified-eligible",
        "Country Z": "unchecked",
    }


def test_get_returns_an_unchecked_record_for_an_unknown_country(travel_vault):
    import eligibility
    record = eligibility.EligibilityStore().get("Country Z")
    assert record.state == "unchecked"
    assert record.source is None and record.as_of is None


# =====================================================================
# Provenance requirements
# =====================================================================

def test_verified_states_require_a_source_and_an_as_of(travel_vault):
    import eligibility
    import validators as V
    with pytest.raises(V.ValidationError):
        eligibility.EligibilityRecord.build("Country A", "verified-eligible", as_of="2026-08-01")
    with pytest.raises(V.ValidationError):
        eligibility.EligibilityRecord.build("Country A", "verified-eligible", source="portal",
                                            as_of=None)


def test_unchecked_needs_neither(travel_vault):
    import eligibility
    record = eligibility.EligibilityRecord.build("Country A", "unchecked")
    assert record.state == "unchecked"


def test_set_state_defaults_as_of_to_today_for_verified(travel_vault):
    import eligibility
    store = eligibility.EligibilityStore().set_state(
        "Country A", "verified-eligible", source="official portal",
    )
    assert store.get("Country A").as_of is not None


def test_invalid_state_is_rejected(travel_vault):
    import eligibility
    import validators as V
    with pytest.raises(V.ValidationError):
        eligibility.EligibilityRecord.build("Country A", "probably-fine")


def test_store_round_trips_through_disk(travel_vault):
    import eligibility
    eligibility.save(_store(eligibility))
    reloaded = eligibility.load()
    assert reloaded.get("Country A").state == "verified-eligible"
    assert reloaded.get("Country A").source == "official portal"
    assert reloaded.assert_proposable("Country A").is_verified_eligible
    with pytest.raises(eligibility.EligibilityRefused):
        reloaded.assert_proposable("Country B")


def test_hand_edited_unquoted_as_of_date_survives_load(travel_vault):
    import eligibility
    eligibility.save(_store(eligibility))
    path = travel_vault / "Travel" / "Entry Eligibility.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("'2026-08-01'", "2026-08-01"),
        encoding="utf-8",
    )
    assert eligibility.load().get("Country A").as_of == "2026-08-01"
