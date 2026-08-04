"""Preference profile v2: multiple relationships, independent dials, seeds by reference."""

from __future__ import annotations

import pytest

# =====================================================================
# Places carry MULTIPLE relationships at once
# =====================================================================

def test_a_place_holds_several_relationships_at_once(travel_vault):
    import preferences
    pref = preferences.PlacePreference.build(
        "Placeholder City",
        ["for-a-person", "been-done", "love-the-place"],
        country="Country A",
    )
    assert set(pref.relationships) == {"for-a-person", "been-done", "love-the-place"}
    assert len(pref.relationships) == 3


def test_relationships_normalize_and_deduplicate_into_canonical_order(travel_vault):
    import preferences
    pref = preferences.PlacePreference.build(
        "Placeholder City", ["been_done", "BEEN-DONE", "want to go"],
    )
    assert pref.relationships == ("been-done", "want-to-go")


def test_at_least_one_relationship_is_required(travel_vault):
    import preferences
    import validators as V
    with pytest.raises(V.ValidationError):
        preferences.PlacePreference.build("Placeholder City", [])


def test_unknown_relationship_is_rejected(travel_vault):
    import preferences
    import validators as V
    with pytest.raises(V.ValidationError):
        preferences.PlacePreference.build("Placeholder City", ["sort-of-curious"])


def test_places_with_finds_every_place_carrying_a_relationship(travel_vault):
    import preferences
    prof = (preferences.PreferenceProfile()
            .upsert_place(preferences.PlacePreference.build("City One", ["been-done", "want-to-go"]))
            .upsert_place(preferences.PlacePreference.build("City Two", ["want-to-go"]))
            .upsert_place(preferences.PlacePreference.build("City Three", ["never-mind-it"])))
    assert {p.place for p in prof.places_with("want-to-go")} == {"City One", "City Two"}
    assert {p.place for p in prof.places_with("been-done")} == {"City One"}


# =====================================================================
# Novelty and depth are INDEPENDENT
# =====================================================================

def test_both_dials_can_be_maxed_at_once(travel_vault):
    """Wanting new places and wanting them properly is not a contradiction."""
    import preferences
    dials = preferences.Dials.build(novelty=1.0, depth=1.0)
    assert dials.novelty == 1.0
    assert dials.depth == 1.0


def test_setting_one_dial_never_moves_the_other(travel_vault):
    import preferences
    prof = preferences.PreferenceProfile().with_dials(novelty=0.9, depth=0.2)
    assert (prof.dials.novelty, prof.dials.depth) == (0.9, 0.2)
    prof = prof.with_dials(novelty=0.1, depth=0.2)
    assert prof.dials.depth == 0.2, "depth must not be derived from novelty"
    assert prof.dials.novelty + prof.dials.depth != pytest.approx(1.0)


def test_dials_must_stay_inside_zero_to_one(travel_vault):
    import preferences
    import validators as V
    with pytest.raises(V.ValidationError):
        preferences.Dials.build(novelty=1.4, depth=0.5)
    with pytest.raises(V.ValidationError):
        preferences.Dials.build(novelty=-0.1, depth=0.5)


# =====================================================================
# Seeds apply BY REFERENCE
# =====================================================================

def _profile_with_seed(preferences):
    return (preferences.PreferenceProfile()
            .upsert_seed(preferences.Seed.build("slow-mornings", "Slow mornings",
                                                {"start_hour": 10}))
            .upsert_place(preferences.PlacePreference.build(
                "Placeholder City", ["want-to-go"], seed_refs=["slow-mornings"])))


def test_editing_a_seed_reaches_every_place_that_references_it(travel_vault):
    import preferences
    prof = _profile_with_seed(preferences)
    assert prof.resolve_place("Placeholder City")["resolved_seeds"][0]["values"] == {"start_hour": 10}

    prof = prof.upsert_seed(preferences.Seed.build("slow-mornings", "Slow mornings",
                                                   {"start_hour": 11}))
    resolved = prof.resolve_place("Placeholder City")
    assert resolved["resolved_seeds"][0]["values"] == {"start_hour": 11}


def test_the_place_record_stores_the_reference_not_a_copy(travel_vault):
    import preferences
    prof = _profile_with_seed(preferences)
    stored = prof.place("Placeholder City").to_dict()
    assert stored["seed_refs"] == ["slow-mornings"]
    assert "values" not in stored
    assert "start_hour" not in repr(stored)


def test_a_dangling_seed_reference_is_surfaced_not_silently_dropped(travel_vault):
    import preferences
    prof = preferences.PreferenceProfile().upsert_place(
        preferences.PlacePreference.build("Placeholder City", ["want-to-go"],
                                          seed_refs=["no-such-seed"]),
    )
    resolved = prof.resolve_place("Placeholder City")
    assert resolved["dangling_seed_refs"] == ["no-such-seed"]
    assert resolved["resolved_seeds"] == []


def test_resolving_an_unknown_place_raises(travel_vault):
    import preferences
    with pytest.raises(KeyError):
        preferences.PreferenceProfile().resolve_place("Nowhere")


# =====================================================================
# Time budget
# =====================================================================

def test_work_block_comes_out_of_a_work_day_only(travel_vault):
    import preferences
    budget = preferences.TimeBudget.build(
        10.0, {"start": "09:00", "end": "13:00", "days": ["mon", "tue"]},
    )
    assert budget.work_block.hours == 4.0
    assert budget.effective_discretionary_hours(work_day=False) == 10.0
    assert budget.effective_discretionary_hours(work_day=True) == 6.0


def test_effective_hours_floor_at_zero(travel_vault):
    import preferences
    budget = preferences.TimeBudget.build(3.0, {"start": "09:00", "end": "17:00"})
    assert budget.effective_discretionary_hours(work_day=True) == 0.0


def test_no_work_block_means_the_full_budget_every_day(travel_vault):
    import preferences
    budget = preferences.TimeBudget.build(8.0)
    assert budget.effective_discretionary_hours(work_day=True) == 8.0


def test_work_block_rejects_reversed_and_malformed_times(travel_vault):
    import preferences
    import validators as V
    with pytest.raises(V.ValidationError):
        preferences.WorkBlock.build("17:00", "09:00")
    with pytest.raises(V.ValidationError):
        preferences.WorkBlock.build("9am", "5pm")


# =====================================================================
# Persistence
# =====================================================================

def test_profile_round_trips_through_disk(travel_vault):
    import preferences
    prof = (_profile_with_seed(preferences)
            .with_dials(0.8, 0.7)
            .with_time_budget(9.0, {"start": "09:00", "end": "12:00", "days": ["mon"]}))
    preferences.save(prof)

    reloaded = preferences.load()
    assert reloaded.version == 2
    assert (reloaded.dials.novelty, reloaded.dials.depth) == (0.8, 0.7)
    assert reloaded.time_budget.work_block.days == ("mon",)
    assert reloaded.place("Placeholder City").relationships == ("want-to-go",)
    assert reloaded.resolve_place("Placeholder City")["resolved_seeds"][0]["values"] == {
        "start_hour": 10,
    }


def test_empty_profile_loads_with_defaults(travel_vault):
    import preferences
    prof = preferences.load()
    assert prof.version == 2
    assert prof.places == () and prof.seeds == ()
    assert prof.time_budget.discretionary_hours_per_day > 0
