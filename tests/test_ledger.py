"""Been-there ledger: the provenance rule, including its negative control.

The rule under test: an inference the traveler did NOT confirm counts as NOT
visited and is committed as such. It is never left pending and never counted as
a visit.
"""

from __future__ import annotations

import pytest

# =====================================================================
# NEGATIVE CONTROL — the whole point of the ledger
# =====================================================================

def test_unconfirmed_inference_does_not_count_as_visited(travel_vault):
    """NEGATIVE CONTROL: inferring a visit must not produce a visit."""
    import ledger
    led = ledger.BeenThereLedger().record_inference(
        "Country A", claim=True, source="itinerary parse",
    )
    record = led.get("Country A")

    assert record is not None, "the inference must be COMMITTED, not dropped"
    assert record.visited is False, "an unconfirmed inference is NOT a visit"
    assert record.provenance == "inferred"
    assert record.claim is True, "the guess is preserved, just not counted"
    assert led.is_visited("Country A") is False
    assert led.count_visited() == 0
    assert led.visited_countries() == ()


def test_unconfirmed_inference_is_committed_to_disk_not_pending(travel_vault):
    """It reaches the file as not-visited. There is no pending queue to leak into."""
    import ledger
    ledger.save(ledger.load().record_inference("Country A", source="itinerary parse"))

    reloaded = ledger.load()
    assert reloaded.get("Country A") is not None
    assert reloaded.count_visited() == 0
    assert reloaded.get("Country A").visited is False

    body = (travel_vault / "Travel" / "Been There.md").read_text(encoding="utf-8")
    assert "Country A" in body


def test_there_is_no_pending_provenance(travel_vault):
    """A pending state is what the rule exists to prevent, so none may exist."""
    import validators as V
    assert set(V.PROVENANCE_VALUES) == {"inferred", "confirmed", "asked", "unknown"}
    assert not any("pend" in p for p in V.PROVENANCE_VALUES)


def test_only_confirmed_provenance_can_carry_a_visit(travel_vault):
    import ledger
    for provenance in ("inferred", "asked", "unknown"):
        record = ledger.CountryRecord.build("Country A", provenance=provenance, claim=True)
        assert record.visited is False, f"{provenance} must never count as a visit"
    confirmed = ledger.CountryRecord.build("Country A", provenance="confirmed", claim=True)
    assert confirmed.visited is True


def test_rule_is_reapplied_on_load_so_a_hand_edit_cannot_forge_a_visit(travel_vault):
    """Enforced in code, not merely documented: a doctored file is corrected on read."""
    import ledger
    forged = {"records": [{
        "country": "Country A", "visited": True, "provenance": "inferred",
        "claim": True, "source": "hand edit", "as_of": "2026-01-01",
    }]}
    led = ledger.BeenThereLedger.from_dict(forged)
    assert led.get("Country A").visited is False
    assert led.count_visited() == 0


# =====================================================================
# POSITIVE CONTROLS — confirmation is the only path to a counted visit
# =====================================================================

def test_confirmation_upgrades_an_inference_to_a_visit(travel_vault):
    import ledger
    led = ledger.BeenThereLedger().record_inference("Country A", source="itinerary parse")
    assert led.count_visited() == 0

    led = led.confirm("Country A", visited=True, source="traveler")
    record = led.get("Country A")
    assert record.visited is True
    assert record.provenance == "confirmed"
    assert led.count_visited() == 1
    assert led.visited_countries() == ("Country A",)


def test_denial_records_asked_and_still_does_not_count(travel_vault):
    import ledger
    led = ledger.BeenThereLedger().confirm("Country A", visited=False, source="traveler")
    record = led.get("Country A")
    assert record.provenance == "asked"
    assert record.visited is False
    assert led.count_visited() == 0


def test_confirmation_can_be_revoked(travel_vault):
    import ledger
    led = ledger.BeenThereLedger().confirm("Country A", visited=True, source="traveler")
    assert led.count_visited() == 1
    led = led.confirm("Country A", visited=False, source="traveler correction")
    assert led.count_visited() == 0
    assert led.get("Country A").provenance == "asked"


# =====================================================================
# Bookkeeping
# =====================================================================

def test_unconfirmed_returns_the_questions_worth_asking(travel_vault):
    import ledger
    led = (ledger.BeenThereLedger()
           .record_inference("Country A", source="parse")
           .record_inference("Country B", source="parse")
           .confirm("Country C", visited=True, source="traveler"))
    assert {r.country for r in led.unconfirmed()} == {"Country A", "Country B"}
    assert led.count_visited() == 1


def test_upsert_is_keyed_case_insensitively(travel_vault):
    import ledger
    led = (ledger.BeenThereLedger()
           .record_inference("Country A", source="parse")
           .confirm("country a", visited=True, source="traveler"))
    assert len(led.records) == 1
    assert led.count_visited() == 1


def test_ledger_round_trips_through_disk(travel_vault):
    import ledger
    led = (ledger.BeenThereLedger()
           .confirm("Country A", visited=True, source="traveler")
           .record_inference("Country B", source="parse"))
    ledger.save(led)
    reloaded = ledger.load()
    assert reloaded.count_visited() == 1
    assert reloaded.get("Country B").provenance == "inferred"
    assert reloaded.get("Country B").visited is False


def test_empty_ledger_loads_clean(travel_vault):
    import ledger
    assert ledger.load().records == ()
    assert ledger.load().count_visited() == 0


def test_invalid_provenance_is_rejected(travel_vault):
    import ledger
    import validators as V
    with pytest.raises(V.ValidationError):
        ledger.CountryRecord.build("Country A", provenance="probably")
