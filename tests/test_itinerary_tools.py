"""The MCP tool layer for the itinerary model.

Includes the registry regression: the original 21 tools must still be present and
callable under their original names. This surface is additive, so a rename here
would be a silent breaking change for every existing caller.
"""

from __future__ import annotations

import json

import pytest

ORIGINAL_21 = {
    "healthcheck", "get_travel_profile", "update_travel_profile_section",
    "list_companion_profiles", "get_companion_profile", "upsert_companion_profile",
    "save_trip_plan", "list_trip_plans", "get_trip_plan",
    "analyze_route", "pricing_reality_check", "geo_pricing_arbitrage",
    "timing_sweet_spot", "fare_rules_analysis", "channel_comparison",
    "tracking_strategy",
    "trip_prep_brief", "emergency_travel_card", "compare_trips",
    "post_trip_review", "price_drop_analysis",
}

NEW_TOOLS = {
    "save_itinerary", "get_itinerary", "list_itineraries", "add_itinerary_segment",
    "itinerary_open_windows", "plan_window_packing", "dwell_time_estimate",
    "get_preference_profile", "set_travel_dials", "set_time_budget",
    "upsert_place_preference", "upsert_preference_seed", "resolve_place_preference",
    "record_country_inference", "confirm_country_visit", "get_been_there_ledger",
    "set_entry_eligibility", "get_entry_eligibility", "filter_proposable_countries",
    "save_trip_prerequisites", "prerequisite_schedule",
}

WINDOW_START = "2026-09-17"
WINDOW_END = "2026-12-16"
ANCHORS = [
    {"city": "Singapore", "country": "Singapore", "arrive": "2026-10-08",
     "depart": "2026-10-09", "status": "locked", "scope": "personal"},
    {"city": "Phuket", "country": "Thailand", "arrive": "2026-10-23",
     "depart": "2026-10-27", "status": "locked", "scope": "personal"},
]


def _fn(server, name):
    tool = getattr(server, name)
    return getattr(tool, "fn", tool)


def _save_anchor_trip(server, slug="autumn-window"):
    return _fn(server, "save_itinerary")(
        slug=slug, title="Autumn window", window_start=WINDOW_START,
        window_end=WINDOW_END, segments=list(ANCHORS),
    )


# =====================================================================
# Registry
# =====================================================================

def test_original_21_tools_are_untouched(travel_vault):
    import server
    missing = sorted(n for n in ORIGINAL_21 if not hasattr(server, n))
    assert not missing, f"existing tools disappeared: {missing}"


def test_all_21_new_tools_are_registered(travel_vault):
    import server
    missing = sorted(n for n in NEW_TOOLS if not hasattr(server, n))
    assert not missing, f"new tools missing from the server module: {missing}"
    assert len(ORIGINAL_21 | NEW_TOOLS) == 42
    for name in NEW_TOOLS:
        obj = getattr(server, name)
        assert callable(obj) or hasattr(obj, "fn")


def test_every_new_tool_has_a_docstring(travel_vault):
    import server
    undocumented = sorted(n for n in NEW_TOOLS if not (_fn(server, n).__doc__ or "").strip())
    assert not undocumented


# =====================================================================
# The Done= round trip, through the tools
# =====================================================================

def test_trip_round_trips_through_the_tools_and_returns_21_13_50(travel_vault):
    import server
    saved = _save_anchor_trip(server)
    assert [w["days"] for w in saved["open_windows"]] == [21, 13, 50]
    assert saved["conflicts"] == []

    fetched = _fn(server, "get_itinerary")(slug="autumn-window")
    assert [w["days"] for w in fetched["open_windows"]] == [21, 13, 50]
    assert [s["city"] for s in fetched["trip"]["segments"]] == ["Singapore", "Phuket"]
    assert all(s["scope"] == "personal" for s in fetched["trip"]["segments"])

    windows = _fn(server, "itinerary_open_windows")(slug="autumn-window")
    assert [w["days"] for w in windows["open_windows"]] == [21, 13, 50]
    assert windows["open_days_total"] == 84
    assert windows["window_days"] == 91


def test_adding_a_segment_recomputes_the_windows(travel_vault):
    import server
    _save_anchor_trip(server)
    result = _fn(server, "add_itinerary_segment")(
        slug="autumn-window", city="Placeholder City", country="Country A",
        arrive="2026-09-20", depart="2026-09-25", status="planned",
    )
    assert len(result["trip"]["segments"]) == 3
    assert [w["days"] for w in result["open_windows"]] != [21, 13, 50]


def test_listing_reports_the_saved_trip(travel_vault):
    import server
    _save_anchor_trip(server)
    listed = _fn(server, "list_itineraries")()
    assert listed["count"] == 1
    assert listed["itineraries"][0]["open_windows"] == 3


def test_packing_the_windows_returns_place_ranges(travel_vault):
    import server
    _save_anchor_trip(server)
    packed = _fn(server, "plan_window_packing")(
        slug="autumn-window", hours_of_interest=40, completionism=0.6,
        discretionary_hours=8, overhead_days=1,
    )
    assert packed["dwell"]["days_low"] == 3
    assert packed["dwell"]["days_high"] == 4
    first = packed["windows"][0]
    assert first["days"] == 21
    assert first["places_low"] == 4 and first["places_high"] == 5


def test_packing_falls_back_to_the_saved_discretionary_hours(travel_vault):
    import server
    _save_anchor_trip(server)
    _fn(server, "set_time_budget")(discretionary_hours_per_day=4.0)
    packed = _fn(server, "plan_window_packing")(slug="autumn-window", hours_of_interest=40)
    assert packed["dwell"]["basis"]["discretionary_hours"] == 4.0


# =====================================================================
# Preferences, ledger, eligibility, prerequisites through the tools
# =====================================================================

def test_dwell_tool_returns_a_range_not_a_point(travel_vault):
    import server
    out = _fn(server, "dwell_time_estimate")(
        place="Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_of_interest=40,
    )
    assert "days" not in out
    assert (out["days_low"], out["days_high"]) == (3, 4)


def test_place_preference_keeps_every_relationship(travel_vault):
    import server
    _fn(server, "upsert_preference_seed")(key="slow-mornings", label="Slow mornings",
                                          values={"start_hour": 10})
    out = _fn(server, "upsert_place_preference")(
        place="Placeholder City",
        relationships=["for-a-person", "been-done", "love-the-place"],
        country="Country A", seed_refs=["slow-mornings"],
    )
    assert set(out["place"]["relationships"]) == {"for-a-person", "been-done", "love-the-place"}
    resolved = _fn(server, "resolve_place_preference")(place="Placeholder City")
    assert resolved["resolved_seeds"][0]["values"] == {"start_hour": 10}
    assert resolved["dangling_seed_refs"] == []


def test_editing_a_seed_updates_the_referencing_place(travel_vault):
    import server
    _fn(server, "upsert_preference_seed")(key="slow-mornings", values={"start_hour": 10})
    _fn(server, "upsert_place_preference")(place="Placeholder City",
                                           relationships=["want-to-go"],
                                           seed_refs=["slow-mornings"])
    updated = _fn(server, "upsert_preference_seed")(key="slow-mornings", values={"start_hour": 11})
    assert updated["referencing_places"] == ["Placeholder City"]
    resolved = _fn(server, "resolve_place_preference")(place="Placeholder City")
    assert resolved["resolved_seeds"][0]["values"] == {"start_hour": 11}


def test_dials_persist_independently(travel_vault):
    import server
    _fn(server, "set_travel_dials")(novelty=1.0, depth=1.0)
    profile = _fn(server, "get_preference_profile")()
    assert profile["dials"]["novelty"] == 1.0
    assert profile["dials"]["depth"] == 1.0


def test_time_budget_requires_both_ends_of_the_work_block(travel_vault):
    import server
    import validators as V
    with pytest.raises(V.ValidationError):
        _fn(server, "set_time_budget")(discretionary_hours_per_day=8.0, work_start="09:00")


def test_inference_tool_commits_a_non_visit(travel_vault):
    """NEGATIVE CONTROL at the tool boundary."""
    import server
    out = _fn(server, "record_country_inference")(
        country="Country A", source="itinerary parse", claim=True,
    )
    assert out["record"]["visited"] is False
    assert out["record"]["provenance"] == "inferred"
    assert out["count_visited"] == 0

    ledger_view = _fn(server, "get_been_there_ledger")()
    assert ledger_view["count_visited"] == 0
    assert ledger_view["unconfirmed"] == ["Country A"]

    confirmed = _fn(server, "confirm_country_visit")(
        country="Country A", visited=True, source="traveler",
    )
    assert confirmed["record"]["visited"] is True
    assert confirmed["count_visited"] == 1


def test_eligibility_tools_refuse_unchecked_but_still_display_it(travel_vault):
    import server
    _fn(server, "set_entry_eligibility")(
        country="Country A", state="verified-eligible", source="official portal",
        as_of="2026-08-01",
    )
    _fn(server, "set_entry_eligibility")(
        country="Country B", state="verified-ineligible", source="official portal",
        as_of="2026-08-01",
    )
    result = _fn(server, "filter_proposable_countries")(
        countries=["Country A", "Country B", "Country Z"],
    )
    assert [r["country"] for r in result["proposable"]] == ["Country A"]
    assert {r["country"]: r["reason"] for r in result["refused"]} == {
        "Country B": "verified-ineligible", "Country Z": "unchecked",
    }
    shown = _fn(server, "get_entry_eligibility")(countries=["Country A", "Country Z"])
    assert {r["country"]: r["state"] for r in shown["records"]} == {
        "Country A": "verified-eligible", "Country Z": "unchecked",
    }


def test_verified_eligibility_without_a_source_is_refused(travel_vault):
    import server
    import validators as V
    with pytest.raises(V.ValidationError):
        _fn(server, "set_entry_eligibility")(country="Country A", state="verified-eligible")


def test_prerequisite_tools_schedule_backward_from_departure(travel_vault):
    import server
    _save_anchor_trip(server)
    _fn(server, "save_trip_prerequisites")(trip_slug="autumn-window", prerequisites=[
        {"key": "passport-renewal", "kind": "document", "lead_days": 21},
        {"key": "visa-country-a", "kind": "visa-in-advance", "lead_days": 30,
         "hard_cutoff_days": 7, "country": "Country A",
         "depends_on": ["passport-renewal"]},
    ])
    schedule = _fn(server, "prerequisite_schedule")(
        trip_slug="autumn-window", departure=WINDOW_START, today="2026-08-01",
    )
    rows = {r["key"]: r for r in schedule["prerequisites"]}
    assert rows["visa-country-a"]["start_by"] == "2026-08-11"
    assert rows["passport-renewal"]["start_by"] == "2026-07-21"
    assert schedule["overdue"] == ["passport-renewal"]


# =====================================================================
# Cross-cutting
# =====================================================================

def test_new_tools_write_audit_lines(travel_vault):
    import server
    _save_anchor_trip(server)
    _fn(server, "dwell_time_estimate")(
        place="Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_of_interest=40,
    )
    lines = (travel_vault / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    tools = {json.loads(line)["tool"] for line in lines}
    assert {"save_itinerary", "dwell_time_estimate"} <= tools


def test_input_rail_rejects_before_the_audited_body_runs(travel_vault):
    """Same ordering as the existing analyzers: validate, then open the audit span."""
    import server
    import validators as V
    with pytest.raises(V.ValidationError):
        _fn(server, "save_itinerary")(
            slug="bad-trip", title="Bad", window_start="2026-12-16",
            window_end="2026-09-17",
        )
    audit_path = travel_vault / "audit.jsonl"
    records = [
        json.loads(line)
        for line in (audit_path.read_text(encoding="utf-8").splitlines() if audit_path.exists() else [])
    ]
    assert not any(r["tool"] == "save_itinerary" for r in records)
    # Nothing was written at all, and no file was created for a call that never ran.
    assert records == []
    # Positive control: a valid call on the same tool DOES produce a line.
    _save_anchor_trip(server)
    assert any(
        json.loads(line)["tool"] == "save_itinerary"
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    )


def test_bad_segment_status_is_rejected_at_the_tool_boundary(travel_vault):
    import server
    import validators as V
    with pytest.raises(V.ValidationError):
        _fn(server, "save_itinerary")(
            slug="bad-status", title="Bad", window_start=WINDOW_START, window_end=WINDOW_END,
            segments=[{**ANCHORS[0], "status": "maybe"}],
        )


def test_healthcheck_creates_the_new_folders(travel_vault):
    import server
    result = _fn(server, "healthcheck")()
    assert result["ok"] is True
    assert (travel_vault / "Travel" / "Itineraries").exists()
    assert (travel_vault / "Travel" / "Prerequisites").exists()
