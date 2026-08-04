"""Itinerary model: ordered segments, open windows, persistence round trip.

The canonical vector is the one in the ticket's Done= line: a 2026-09-17 to
2026-12-16 window with two locked anchors yields open windows of 21, 13 and 50
days. Everything else in this file exists to keep that number honest.
"""

from __future__ import annotations

import pytest

WINDOW_START = "2026-09-17"
WINDOW_END = "2026-12-16"

ANCHOR_ONE = {
    "city": "Singapore", "country": "Singapore",
    "arrive": "2026-10-08", "depart": "2026-10-09",
    "status": "locked", "scope": "personal",
}
ANCHOR_TWO = {
    "city": "Phuket", "country": "Thailand",
    "arrive": "2026-10-23", "depart": "2026-10-27",
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
    assert (windows[0].start, windows[0].end) == ("2026-09-17", "2026-10-07")
    assert (windows[1].start, windows[1].end) == ("2026-10-10", "2026-10-22")
    assert (windows[2].start, windows[2].end) == ("2026-10-28", "2026-12-16")
    assert trip.window_days == 91


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
    assert [s.city for s in trip.segments] == ["Singapore", "Phuket"]
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
    """YAML turns an unquoted 2026-10-08 into a date object. Loading coerces it."""
    import profile

    import itinerary
    itinerary.save(_trip(itinerary))
    path = travel_vault / "Travel" / "Itineraries" / "autumn-window.md"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("'2026-10-08'", "2026-10-08"), encoding="utf-8")
    # The frontmatter really did change type under us...
    from datetime import date as _date
    doc = profile.read_data_doc("Itineraries/autumn-window.md")
    assert any(isinstance(s.get("arrive"), _date) for s in doc["frontmatter"]["segments"])
    # ...and the model still loads it as an ISO string with the same windows.
    reloaded = itinerary.load("autumn-window")
    assert reloaded.segments[0].arrive == "2026-10-08"
    assert [w.days for w in reloaded.open_windows()] == EXPECTED_WINDOW_DAYS


# --------------------------------- segments ---------------------------------

def test_scope_defaults_to_personal_and_accepts_company(travel_vault):
    """The company lane is empty today; the field exists from the first migration."""
    import itinerary
    personal = itinerary.Segment.build("Placeholder City", "Country A", "2026-10-01", "2026-10-03")
    company = itinerary.Segment.build(
        "Placeholder City", "Country A", "2026-10-01", "2026-10-03", scope="company",
    )
    assert personal.scope == "personal"
    assert company.scope == "company"
    assert "scope" in personal.to_dict()


def test_segment_rejects_bad_status_scope_and_reversed_dates(travel_vault):
    import itinerary
    import validators as V
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", "2026-10-01", "2026-10-03", status="tentative")
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", "2026-10-01", "2026-10-03", scope="team")
    with pytest.raises(V.ValidationError):
        itinerary.Segment.build("A", "Country A", "2026-10-05", "2026-10-01")


def test_status_and_scope_normalize_underscores(travel_vault):
    import itinerary
    seg = itinerary.Segment.build(
        "A", "Country A", "2026-10-01", "2026-10-03", status="LOCKED", scope=" Personal ",
    )
    assert (seg.status, seg.scope) == ("locked", "personal")


def test_segment_day_and_night_counts(travel_vault):
    import itinerary
    seg = itinerary.Segment.build("A", "Country A", "2026-10-08", "2026-10-09")
    assert seg.nights == 1
    assert seg.days == 2  # arrive day and depart day are both spent there


# ------------------------------ status handling ------------------------------

def test_candidate_does_not_consume_a_window_by_default(travel_vault):
    """A candidate is a proposal to FILL a window, so it must not eat one."""
    import itinerary
    candidate = {
        "city": "Placeholder City", "country": "Country A",
        "arrive": "2026-09-20", "depart": "2026-09-25",
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
        "arrive": "2026-09-20", "depart": "2026-09-25",
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
         "arrive": "2026-10-09", "depart": "2026-10-12",
         "status": "locked", "scope": "personal"},
    ))
    assert trip.conflicts() == []


def test_strict_overlap_is_reported(travel_vault):
    import itinerary
    trip = _trip(itinerary, segments=(
        ANCHOR_ONE,
        {"city": "Placeholder City", "country": "Country A",
         "arrive": "2026-10-08", "depart": "2026-10-12",
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
         "arrive": "2027-01-05", "depart": "2027-01-09",
         "status": "planned", "scope": "personal"},
    ))
    assert len(trip.out_of_window()) == 1
    # It sits past window_end, so it cannot carve a window inside it.
    assert trip.open_windows()[-1].end == WINDOW_END


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
         "arrive": WINDOW_START, "depart": "2026-09-19",
         "status": "locked", "scope": "personal"},
    ))
    windows = trip.open_windows()
    assert len(windows) == 1
    assert windows[0].start == "2026-09-20"


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
    windows = _trip(itinerary).open_windows()
    assert (windows[0].after, windows[0].before) == (None, "Singapore")
    assert (windows[1].after, windows[1].before) == ("Singapore", "Phuket")
    assert (windows[2].after, windows[2].before) == ("Phuket", None)


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
