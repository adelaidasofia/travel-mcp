"""Itinerary model: ordered segments, open windows, persistence round trip.

The canonical vector: a 91-day window with two locked anchors yields open
windows of 21, 13 and 50 days. Everything else in this file exists to keep that
number honest.

Every date is a DAY OFFSET from a neutral synthetic base, because the vector is
a property of the arithmetic and not of any real calendar. Places are the same
neutral placeholders the rest of the suite uses.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

#: Arbitrary synthetic base. Nothing depends on which day it is.
BASE = date(2030, 3, 1)
WINDOW_LENGTH_DAYS = 91


def day(offset: int) -> str:
    """ISO date `offset` days after the window start (offset 0 == window start)."""
    return (BASE + timedelta(days=offset)).isoformat()


WINDOW_START = day(0)
WINDOW_END = day(WINDOW_LENGTH_DAYS - 1)

#: Anchor 1 opens on day 21, so window 1 is days 0..20 = 21 days. It occupies
#: 2 days (arrive + depart), leaving days 23..35 = 13 days before anchor 2.
ANCHOR_ONE = {
    "city": "Placeholder City", "country": "Country A",
    "arrive": day(21), "depart": day(22),
    "status": "locked", "scope": "personal",
}
#: Anchor 2 occupies 5 days, leaving days 41..90 = 50 days to the window end.
ANCHOR_TWO = {
    "city": "Second City", "country": "Country C",
    "arrive": day(36), "depart": day(40),
    "status": "locked", "scope": "personal",
}
EXPECTED_WINDOW_DAYS = [21, 13, 50]


def _trip(itinerary, segments=(ANCHOR_ONE, ANCHOR_TWO), **kw):
    return itinerary.Trip.build(
        slug=kw.pop("slug", "autumn-window"),
        title=kw.pop("title", "Autumn window"),
        window_start=kw.pop("window_start", WINDOW_START),
        window_end=kw.pop("window_end", WINDOW_END),
        segments=list(segments),
        **kw,
    )


# ------------------------------ the Done= vector ------------------------------

def test_two_locked_anchors_yield_21_13_50(travel_vault):
    import itinerary
    trip = _trip(itinerary)
    windows = trip.open_windows()
    assert [w.days for w in windows] == EXPECTED_WINDOW_DAYS
    assert (windows[0].start, windows[0].end) == (day(0), day(20))
    assert (windows[1].start, windows[1].end) == (day(23), day(35))
    assert (windows[2].start, windows[2].end) == (day(41), day(90))
    assert trip.window_days == WINDOW_LENGTH_DAYS == 91


def test_open_days_plus_anchor_days_equal_the_window(travel_vault):
    """No day is double-counted and none goes missing."""
    import itinerary
    trip = _trip(itinerary)
    open_days = sum(w.days for w in trip.open_windows())
    anchor_days = sum(s.days for s in trip.segments)
    assert open_days + anchor_days == trip.window_days


def test_segments_sort_by_arrival_regardless_of_input_order(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(ANCHOR_TWO, ANCHOR_ONE))
    assert [s.city for s in trip.segments] == [ANCHOR_ONE["city"], ANCHOR_TWO["city"]]
    assert [w.days for w in trip.open_windows()] == EXPECTED_WINDOW_DAYS


def test_persistence_round_trip_preserves_the_windows(travel_vault):
    import itinerary
    saved = itinerary.save(_trip(itinerary))
    assert saved["bytes"] > 0
    reloaded = itinerary.load("autumn-window")
    assert [s.to_dict() for s in reloaded.segments] == [
        {**ANCHOR_ONE, "note": None}, {**ANCHOR_TWO, "note": None},
    ]
    assert [w.days for w in reloaded.open_windows()] == EXPECTED_WINDOW_DAYS


def test_hand_edited_unquoted_dates_survive_load(travel_vault):
    """YAML turns an unquoted ISO date into a date object. Loading coerces it."""
    import profile

    import itinerary
    itinerary.save(_trip(itinerary))
    path = travel_vault / "Travel" / "Itineraries" / "autumn-window.md"
    raw = path.read_text(encoding="utf-8")
    arrive = ANCHOR_ONE["arrive"]
    assert f"'{arrive}'" in raw  # positive control: it really was quoted on write
    path.write_text(raw.replace(f"'{arrive}'", arrive), encoding="utf-8")
    # The frontmatter really did change type under us...
    doc = profile.read_data_doc("Itineraries/autumn-window.md")
    assert any(isinstance(s.get("arrive"), date) for s in doc["frontmatter"]["segments"])
    # ...and the model still loads it as an ISO string with the same windows.
    reloaded = itinerary.load("autumn-window")
    assert reloaded.segments[0].arrive == arrive
    assert [w.days for w in reloaded.open_windows()] == EXPECTED_WINDOW_DAYS


# --------------------------------- segments ---------------------------------

def test_scope_defaults_to_personal_and_accepts_company(travel_vault):
    """The company lane is empty today; the field exists from the first migration."""
    import itinerary
    personal = itinerary.Segment.build("Placeholder City", "Country A", day(30), day(32))
    company = itinerary.Segment.build(
        "Placeholder City", "Country A", day(30), day(32), scope="company",
    )
    assert personal.scope == "personal"
    assert company.scope == "company"
    assert "scope" in personal.to_dict()


def test_segment_rejects_bad_status_scope_and_reversed_dates(travel_vault):
    import itinerary
    import validators as V
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", day(30), day(32), status="tentative")
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", day(30), day(32), scope="team")
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", day(34), day(30))


def test_status_and_scope_normalize_underscores(travel_vault):
    import itinerary
    seg = itinerary.Segment.build(
        "A", "Country A", day(30), day(32), status="LOCKED", scope=" Personal ",
    )
    assert (seg.status, seg.scope) == ("locked", "personal")


def test_segment_day_and_night_counts(travel_vault):
    import itinerary
    seg = itinerary.Segment.build("A", "Country A", day(21), day(22))
    assert seg.nights == 1
    assert seg.days == 2  # arrive day and depart day are both spent there


# ------------------------------ status handling ------------------------------

def test_candidate_does_not_consume_a_window_by_default(travel_vault):
    """A candidate is a proposal to FILL a window, so it must not eat one."""
    import itinerary
    candidate = {
        "city": "Placeholder City", "country": "Country A",
        "arrive": day(3), "depart": day(8),
        "status": "candidate", "scope": "personal",
    }
    trip = _trip(itinerary, segments=(ANCHOR_ONE, ANCHOR_TWO, candidate))
    assert [w.days for w in trip.open_windows()] == EXPECTED_WINDOW_DAYS
    with_candidates = trip.open_windows(("locked", "planned", "candidate"))
    assert [w.days for w in with_candidates] != EXPECTED_WINDOW_DAYS
    assert sum(w.days for w in with_candidates) < sum(w.days for w in trip.open_windows())


def test_planned_segments_consume_windows(travel_vault):
    import itinerary
    planned = {
        "city": "Placeholder City", "country": "Country A",
        "arrive": day(3), "depart": day(8),
        "status": "planned", "scope": "personal",
    }
    trip = _trip(itinerary, segments=(ANCHOR_ONE, ANCHOR_TWO, planned))
    assert [w.days for w in trip.open_windows()] != EXPECTED_WINDOW_DAYS


# --------------------------------- conflicts ---------------------------------

def test_touching_endpoints_is_a_travel_day_not_a_conflict(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        ANCHOR_ONE,
        {"city": "Placeholder City", "country": "Country A",
         "arrive": day(22), "depart": day(25),
         "status": "locked", "scope": "personal"},
    ))
    assert trip.conflicts() == []


def test_strict_overlap_is_reported(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        ANCHOR_ONE,
        {"city": "Placeholder City", "country": "Country A",
         "arrive": day(21), "depart": day(25),
         "status": "locked", "scope": "personal"},
    ))
    conflicts = trip.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["overlap_days"] == 1


def test_segment_outside_the_window_is_surfaced(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        ANCHOR_ONE,
        {"city": "Placeholder City", "country": "Country A",
         "arrive": day(110), "depart": day(114),
         "status": "planned", "scope": "personal"},
    ))
    assert len(trip.out_of_window()) == 1
    # It sits past window_end, so it cannot carve a window inside it.
    assert trip.open_windows()[-1].end == WINDOW_END


def test_conflicts_reports_every_overlapping_pair_not_just_neighbours(travel_vault):
    """NEGATIVE CONTROL: a long stay can swallow several later ones.

    A(0..19) contains both B(1..2) and C(9..10). Comparing only ADJACENT pairs
    reported (A,B) alone — so a caller could fix every conflict it was shown and
    still be double-booked with C.
    """
    import itinerary
    trip = _trip(itinerary, segments=(
        {"city": "A", "country": "Country A", "arrive": day(0), "depart": day(19),
         "status": "locked", "scope": "personal"},
        {"city": "B", "country": "Country A", "arrive": day(1), "depart": day(2),
         "status": "locked", "scope": "personal"},
        {"city": "C", "country": "Country A", "arrive": day(9), "depart": day(10),
         "status": "locked", "scope": "personal"},
    ))
    pairs = [(c["first"]["city"], c["second"]["city"]) for c in trip.conflicts()]
    assert pairs == [("A", "B"), ("A", "C")]
    # Overlap is clipped to the shorter stay, not to the container's length.
    assert [c["overlap_days"] for c in trip.conflicts()] == [1, 1]


def test_conflicts_stay_empty_when_nothing_overlaps(travel_vault):
    """POSITIVE CONTROL for the all-pairs walk: three disjoint stays, no report."""
    import itinerary
    trip = _trip(itinerary, segments=(
        {"city": "A", "country": "Country A", "arrive": day(0), "depart": day(2),
         "status": "locked", "scope": "personal"},
        {"city": "B", "country": "Country A", "arrive": day(5), "depart": day(6),
         "status": "locked", "scope": "personal"},
        {"city": "C", "country": "Country A", "arrive": day(9), "depart": day(10),
         "status": "locked", "scope": "personal"},
    ))
    assert trip.conflicts() == []


def test_proposed_countries_lists_candidates_only(travel_vault):
    """The eligibility gate reads this; a locked stay is a fact, not a proposal."""
    import itinerary
    trip = _trip(itinerary, segments=(
        ANCHOR_ONE,
        {"city": "Placeholder City", "country": "Country D", "arrive": day(3),
         "depart": day(8), "status": "candidate", "scope": "personal"},
        {"city": "Third City", "country": "Country D", "arrive": day(50),
         "depart": day(52), "status": "candidate", "scope": "personal"},
        {"city": "Fourth City", "country": "Country E", "arrive": day(60),
         "depart": day(62), "status": "planned", "scope": "personal"},
    ))
    # De-duplicated, and neither the locked anchor nor the planned stay appears.
    assert trip.proposed_countries() == ("Country D",)
    assert _trip(itinerary).proposed_countries() == ()


# --------------------------------- edge cases ---------------------------------

def test_empty_trip_is_one_whole_window(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=())
    windows = trip.open_windows()
    assert len(windows) == 1
    assert windows[0].days == trip.window_days == 91


def test_anchor_on_the_first_day_leaves_no_leading_window(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        {"city": "Placeholder City", "country": "Country A",
         "arrive": WINDOW_START, "depart": day(2),
         "status": "locked", "scope": "personal"},
    ))
    windows = trip.open_windows()
    assert len(windows) == 1
    assert windows[0].start == day(3)


def test_fully_booked_window_has_no_open_windows(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        {"city": "Placeholder City", "country": "Country A",
         "arrive": WINDOW_START, "depart": WINDOW_END,
         "status": "locked", "scope": "personal"},
    ))
    assert trip.open_windows() == []


def test_window_labels_name_the_neighbouring_segments(travel_vault):
    import itinerary
    first, second = ANCHOR_ONE["city"], ANCHOR_TWO["city"]
    windows = _trip(itinerary).open_windows()
    assert (windows[0].after, windows[0].before) == (None, first)
    assert (windows[1].after, windows[1].before) == (first, second)
    assert (windows[2].after, windows[2].before) == (second, None)


def test_list_all_reports_counts(travel_vault):
    import itinerary
    itinerary.save(_trip(itinerary))
    itinerary.save(_trip(itinerary, slug="second-trip", title="Second trip", segments=()))
    listed = {row["slug"]: row for row in itinerary.list_all()}
    assert listed["autumn-window"]["segments"] == 2
    assert listed["autumn-window"]["open_windows"] == 3
    assert listed["second-trip"]["open_windows"] == 1


def test_load_missing_itinerary_raises(travel_vault):
    import itinerary
    with pytest.raises(FileNotFoundError):
        itinerary.load("no-such-trip")


def test_slug_cannot_escape_the_travel_folder(travel_vault):
    """Defence in depth: the slug rail strips separators before the path layer."""
    import itinerary
    trip = _trip(itinerary, slug="../../escape")
    assert "/" not in trip.slug
    itinerary.save(trip)
    assert (travel_vault / "Travel" / "Itineraries" / f"{trip.slug}.md").exists()
