"""Prerequisite graph scheduled backward from departure."""

from __future__ import annotations

import pytest

DEPARTURE = "2026-09-17"

PASSPORT = {
    "key": "passport-renewal", "kind": "document", "label": "Renew passport",
    "lead_days": 21, "hard_cutoff_days": 0,
}
VISA = {
    "key": "visa-country-a", "kind": "visa-in-advance", "label": "Apply for entry visa",
    "lead_days": 30, "hard_cutoff_days": 7, "country": "Country A",
    "depends_on": ["passport-renewal"],
}
VACCINE = {
    "key": "vaccination", "kind": "vaccination", "label": "Required vaccination",
    "lead_days": 3, "hard_cutoff_days": 10,
}


def _by_key(scheduled):
    return {s.key: s for s in scheduled}


# =====================================================================
# Backward scheduling
# =====================================================================

def test_hard_cutoff_pulls_completion_earlier_than_departure(travel_vault):
    import prereqs
    scheduled = _by_key(prereqs.schedule_backward([VACCINE], DEPARTURE, today="2026-08-01"))
    vaccine = scheduled["vaccination"]
    assert vaccine.complete_by == "2026-09-07"  # departure - 10
    assert vaccine.start_by == "2026-09-04"     # complete_by - 3 lead days


def test_a_dependency_must_finish_before_its_dependent_starts(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today="2026-08-01"),
    )
    visa, passport = scheduled["visa-country-a"], scheduled["passport-renewal"]
    assert visa.complete_by == "2026-09-10"   # departure - 7
    assert visa.start_by == "2026-08-11"      # complete_by - 30
    # The passport cannot use its own slack: the visa needs it by 08-11.
    assert passport.complete_by == "2026-08-11"
    assert passport.start_by == "2026-07-21"  # complete_by - 21


def test_a_dependency_chain_cascades_backward(travel_vault):
    import prereqs
    third = {"key": "permit", "kind": "permit", "lead_days": 5,
             "depends_on": ["visa-country-a"]}
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA, third], DEPARTURE, today="2026-08-01"),
    )
    # permit has no cutoff, so it may finish on departure day and start 5 days before
    assert scheduled["permit"].start_by == "2026-09-12"
    # the visa is unaffected by a dependent that starts later than its own deadline
    assert scheduled["visa-country-a"].complete_by == "2026-09-10"


def test_results_are_sorted_by_urgency(travel_vault):
    import prereqs
    scheduled = prereqs.schedule_backward([VACCINE, VISA, PASSPORT], DEPARTURE, today="2026-08-01")
    starts = [s.start_by for s in scheduled]
    assert starts == sorted(starts)
    assert scheduled[0].key == "passport-renewal"


# =====================================================================
# Status against today
# =====================================================================

def test_status_marks_overdue_due_soon_and_on_track(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today="2026-08-01", warn_days=7),
    )
    assert scheduled["passport-renewal"].status == "overdue"      # start_by 2026-07-21
    assert scheduled["passport-renewal"].slack_days == -11
    assert scheduled["visa-country-a"].status == "on-track"       # start_by 2026-08-11
    assert scheduled["visa-country-a"].slack_days == 10


def test_warn_window_widens_due_soon(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today="2026-08-01", warn_days=14),
    )
    assert scheduled["visa-country-a"].status == "due-soon"


def test_summary_lists_the_overdue_keys(travel_vault):
    import prereqs
    scheduled = prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today="2026-08-01")
    summary = prereqs.summarize(scheduled, departure=DEPARTURE)
    assert summary["overdue"] == ["passport-renewal"]
    assert summary["earliest_start_by"] == "2026-07-21"
    assert summary["count"] == 2


# =====================================================================
# Graph integrity — fail loud, never silently drop a prerequisite
# =====================================================================

def test_a_dependency_cycle_raises(travel_vault):
    import prereqs
    a = {"key": "a", "kind": "document", "lead_days": 1, "depends_on": ["b"]}
    b = {"key": "b", "kind": "document", "lead_days": 1, "depends_on": ["a"]}
    with pytest.raises(prereqs.PrerequisiteCycleError):
        prereqs.schedule_backward([a, b], DEPARTURE)


def test_self_dependency_raises(travel_vault):
    import prereqs
    with pytest.raises(prereqs.PrerequisiteCycleError):
        prereqs.Prerequisite.build("a", "document", lead_days=1, depends_on=["a"])


def test_an_unknown_dependency_key_raises(travel_vault):
    import prereqs
    orphan = {"key": "visa", "kind": "visa-in-advance", "lead_days": 10,
              "depends_on": ["nonexistent"]}
    with pytest.raises(prereqs.UnknownPrerequisiteError):
        prereqs.schedule_backward([orphan], DEPARTURE)


def test_duplicate_keys_are_rejected(travel_vault):
    import prereqs
    import validators as V
    with pytest.raises(V.ValidationError):
        prereqs.schedule_backward([PASSPORT, dict(PASSPORT)], DEPARTURE)


def test_invalid_kind_is_rejected(travel_vault):
    import prereqs
    import validators as V
    with pytest.raises(V.ValidationError):
        prereqs.Prerequisite.build("a", "vibes", lead_days=1)


def test_empty_graph_schedules_to_nothing(travel_vault):
    import prereqs
    assert prereqs.schedule_backward([], DEPARTURE) == []
    assert prereqs.summarize([], departure=DEPARTURE)["earliest_start_by"] is None


# =====================================================================
# Persistence
# =====================================================================

def test_prerequisites_round_trip_through_disk(travel_vault):
    import prereqs
    prereqs.save("autumn-window", [PASSPORT, VISA])
    stored = prereqs.load("autumn-window")
    assert {p.key for p in stored} == {"passport-renewal", "visa-country-a"}
    scheduled = _by_key(prereqs.schedule_backward(stored, DEPARTURE, today="2026-08-01"))
    assert scheduled["passport-renewal"].start_by == "2026-07-21"


def test_an_unschedulable_graph_never_lands_on_disk(travel_vault):
    import prereqs
    a = {"key": "a", "kind": "document", "lead_days": 1, "depends_on": ["b"]}
    b = {"key": "b", "kind": "document", "lead_days": 1, "depends_on": ["a"]}
    with pytest.raises(prereqs.PrerequisiteCycleError):
        prereqs.save("bad-trip", [a, b])
    with pytest.raises(FileNotFoundError):
        prereqs.load("bad-trip")


def test_loading_a_missing_graph_raises(travel_vault):
    import prereqs
    with pytest.raises(FileNotFoundError):
        prereqs.load("no-such-trip")
