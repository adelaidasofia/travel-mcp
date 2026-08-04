"""Prerequisite graph scheduled backward from departure.

Every date is expressed as DAYS BEFORE a neutral synthetic departure, because
backward scheduling is arithmetic on offsets and nothing here depends on which
calendar date the trip leaves.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

#: Arbitrary synthetic departure. Nothing depends on which day it is.
DEPARTURE_DATE = date(2030, 3, 1)
DEPARTURE = DEPARTURE_DATE.isoformat()


def before_departure(days: int) -> str:
    """ISO date `days` days before departure."""
    return (DEPARTURE_DATE - timedelta(days=days)).isoformat()


#: "Today" for the status assertions: 47 days out, which leaves the passport
#: (start_by = departure - 58) overdue and the visa (departure - 37) on track.
TODAY = before_departure(47)

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
    scheduled = _by_key(prereqs.schedule_backward([VACCINE], DEPARTURE, today=TODAY))
    vaccine = scheduled["vaccination"]
    assert vaccine.complete_by == before_departure(10)   # hard cutoff
    assert vaccine.start_by == before_departure(13)      # complete_by - 3 lead days


def test_a_dependency_must_finish_before_its_dependent_starts(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today=TODAY),
    )
    visa, passport = scheduled["visa-country-a"], scheduled["passport-renewal"]
    assert visa.complete_by == before_departure(7)     # hard cutoff
    assert visa.start_by == before_departure(37)       # complete_by - 30 lead days
    # The passport cannot use its own slack: the visa needs it 37 days out.
    assert passport.complete_by == before_departure(37)
    assert passport.start_by == before_departure(58)   # complete_by - 21 lead days


def test_a_dependency_chain_cascades_backward(travel_vault):
    import prereqs
    third = {"key": "permit", "kind": "permit", "lead_days": 5,
             "depends_on": ["visa-country-a"]}
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA, third], DEPARTURE, today=TODAY),
    )
    # permit has no cutoff, so it may finish on departure day and start 5 days before
    assert scheduled["permit"].start_by == before_departure(5)
    # the visa is unaffected by a dependent that starts later than its own deadline
    assert scheduled["visa-country-a"].complete_by == before_departure(7)


def test_results_are_sorted_by_urgency(travel_vault):
    import prereqs
    scheduled = prereqs.schedule_backward([VACCINE, VISA, PASSPORT], DEPARTURE, today=TODAY)
    starts = [s.start_by for s in scheduled]
    assert starts == sorted(starts)
    assert scheduled[0].key == "passport-renewal"


# =====================================================================
# Status against today
# =====================================================================

def test_status_marks_overdue_due_soon_and_on_track(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today=TODAY, warn_days=7),
    )
    assert scheduled["passport-renewal"].status == "overdue"      # start_by = departure - 58
    assert scheduled["passport-renewal"].slack_days == -11
    assert scheduled["visa-country-a"].status == "on-track"       # start_by = departure - 37
    assert scheduled["visa-country-a"].slack_days == 10


def test_warn_window_widens_due_soon(travel_vault):
    import prereqs
    scheduled = _by_key(
        prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today=TODAY, warn_days=14),
    )
    assert scheduled["visa-country-a"].status == "due-soon"


def test_summary_lists_the_overdue_keys(travel_vault):
    import prereqs
    scheduled = prereqs.schedule_backward([PASSPORT, VISA], DEPARTURE, today=TODAY)
    summary = prereqs.summarize(scheduled, departure=DEPARTURE)
    assert summary["overdue"] == ["passport-renewal"]
    assert summary["earliest_start_by"] == before_departure(58)
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


def test_duplicate_keys_are_named_on_save_not_reported_as_an_empty_cycle(travel_vault):
    """NEGATIVE CONTROL: `save` used to raise `dependency cycle among: []`.

    Two entries under one key collapse into a single graph node, so the length
    check at the end of the topological sort mismatched and reported a cycle with
    nothing stuck in it — an error message that named neither the problem nor the
    key. It must name the key instead.
    """
    import prereqs
    import validators as V
    with pytest.raises(V.ValidationError) as exc:
        prereqs.save("dupe-trip", [PASSPORT, dict(PASSPORT)])
    assert "passport-renewal" in str(exc.value)
    assert not isinstance(exc.value, prereqs.PrerequisiteCycleError)
    # Nothing landed on disk, same as any other unschedulable graph.
    with pytest.raises(FileNotFoundError):
        prereqs.load("dupe-trip")
    # POSITIVE CONTROL: distinct keys still save.
    prereqs.save("dupe-trip", [PASSPORT, VISA])
    assert {p.key for p in prereqs.load("dupe-trip")} == {"passport-renewal", "visa-country-a"}


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
    scheduled = _by_key(prereqs.schedule_backward(stored, DEPARTURE, today=TODAY))
    assert scheduled["passport-renewal"].start_by == before_departure(58)


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
