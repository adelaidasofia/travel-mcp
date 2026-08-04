"""Structured itinerary model: a trip is ORDERED SEGMENTS, not prose.

This is the primitive everything else is downstream of. Without it there is no
calendar, no map, no events layer and nothing to plan against.

A segment is {city, country, arrive, depart, status, scope}:
  status  locked | planned | candidate
  scope   personal | company

`scope` exists from the first migration even though the company lane starts
empty. Adding it later would mean migrating every stored trip.

OPEN WINDOWS — the boundary convention, stated once so it cannot drift:

  A segment occupies BOTH its arrive day and its depart day. You are in that
  city on both. So the free stretch between two committed segments runs from
  (previous.depart + 1 day) to (next.arrive - 1 day), inclusive on both ends.
  At the edges of the trip window there is no boundary day to give up, so the
  first window starts on window_start and the last ends on window_end.

  Worked example, written as DAY OFFSETS from the window start. The result is a
  property of the arithmetic, not of any particular calendar date:

    91-day window (day 0..day 90), locked day 21..22 and day 36..40
      window 1  day  0..day 20  = 21 days
      window 2  day 23..day 35  = 13 days
      window 3  day 41..day 90  = 50 days

    21 + 13 + 50 open days + 2 + 5 anchor days = the whole 91-day window: no day
    is double-counted and none goes missing.

Two segments touching endpoint-to-endpoint (one departs the day the next
arrives) is a travel day, not a conflict. Only a strict overlap is a conflict.
"""

from __future__ import annotations

import profile as profile_mod
import time
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import validators as V

ITINERARY_TYPE = "travel_itinerary"

#: Statuses that consume calendar time when open windows are computed.
#: A `candidate` is a proposal to FILL a window, so it must not eat one.
COMMITTED_STATUSES: tuple[str, ...] = ("locked", "planned")

#: The one status that makes a segment a PROPOSAL rather than a fact. `locked`
#: and `planned` are things the traveler already committed to; a `candidate` is
#: this system suggesting a place, so it is the status that must clear the
#: entry-eligibility gate before it reaches a caller.
PROPOSAL_STATUS = "candidate"


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def _iso(value: date) -> str:
    return value.isoformat()


@dataclass(frozen=True)
class Segment:
    """One city stay inside a trip."""

    city: str
    country: str
    arrive: str
    depart: str
    status: str = "planned"
    scope: str = "personal"
    note: str | None = None

    @classmethod
    def build(cls, city: Any, country: Any, arrive: Any, depart: Any,
              status: Any = "planned", scope: Any = "personal",
              note: Any = None) -> Segment:
        """Validated constructor. Every load and every tool call goes through here."""
        arrive_iso, depart_iso = V.validate_date_order(
            arrive, depart, start_field="arrive", end_field="depart"
        )
        return cls(
            city=V.validate_name(city, field="city"),
            country=V.normalize_country(country),
            arrive=arrive_iso,
            depart=depart_iso,
            status=V.validate_segment_status(status),
            scope=V.validate_segment_scope(scope),
            note=(V.truncate(note, 400) if isinstance(note, str) and note.strip() else None),
        )

    @property
    def nights(self) -> int:
        return (_d(self.depart) - _d(self.arrive)).days

    @property
    def days(self) -> int:
        """Inclusive day count — arrive and depart are both days spent there."""
        return self.nights + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "country": self.country,
            "arrive": self.arrive,
            "depart": self.depart,
            "status": self.status,
            "scope": self.scope,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Segment:
        if not isinstance(raw, dict):
            raise V.ValidationError("each segment must be an object with city/country/arrive/depart")
        return cls.build(
            city=raw.get("city"),
            country=raw.get("country"),
            arrive=raw.get("arrive"),
            depart=raw.get("depart"),
            status=raw.get("status", "planned"),
            scope=raw.get("scope", "personal"),
            note=raw.get("note"),
        )


@dataclass(frozen=True)
class OpenWindow:
    """An unallocated stretch inside the trip window."""

    index: int
    start: str
    end: str
    days: int
    after: str | None = None
    before: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "days": self.days,
            "after": self.after,
            "before": self.before,
        }


@dataclass(frozen=True)
class Trip:
    """An ordered set of segments inside a departure..return window."""

    slug: str
    title: str
    window_start: str
    window_end: str
    segments: tuple[Segment, ...] = ()
    notes: str | None = None
    scope: str = "personal"
    last_updated: str | None = field(default=None, compare=False)

    @classmethod
    def build(cls, slug: Any, title: Any, window_start: Any, window_end: Any,
              segments: Any = (), notes: Any = None, scope: Any = "personal",
              last_updated: Any = None) -> Trip:
        start_iso, end_iso = V.validate_date_order(
            window_start, window_end, start_field="window_start", end_field="window_end"
        )
        raw_segments = segments or ()
        if isinstance(raw_segments, dict):
            raise V.ValidationError("segments must be a list, not an object")
        built = tuple(
            s if isinstance(s, Segment) else Segment.from_dict(s) for s in raw_segments
        )
        return cls(
            slug=V.validate_slug(slug),
            title=V.validate_name(title, field="title", limit=200),
            window_start=start_iso,
            window_end=end_iso,
            segments=_sorted(built),
            notes=(notes.strip() if isinstance(notes, str) and notes.strip() else None),
            scope=V.validate_segment_scope(scope),
            last_updated=last_updated if isinstance(last_updated, str) else None,
        )

    # ---- derived ----

    @property
    def window_days(self) -> int:
        return (_d(self.window_end) - _d(self.window_start)).days + 1

    def with_segment(self, segment: Segment) -> Trip:
        """Return a new Trip with one more segment, re-sorted."""
        return replace(self, segments=_sorted((*self.segments, segment)))

    def committed(self, statuses: tuple[str, ...] = COMMITTED_STATUSES) -> tuple[Segment, ...]:
        allowed = {V.validate_segment_status(s) for s in statuses}
        return tuple(s for s in self.segments if s.status in allowed)

    def open_windows(self, statuses: tuple[str, ...] = COMMITTED_STATUSES) -> list[OpenWindow]:
        return compute_open_windows(
            self.window_start, self.window_end, self.committed(statuses)
        )

    def conflicts(self, statuses: tuple[str, ...] = COMMITTED_STATUSES) -> list[dict[str, Any]]:
        """EVERY strictly-overlapping committed pair. Endpoint-to-endpoint is a travel day.

        All pairs, not just adjacent ones: a long segment can swallow several
        later ones, and reporting only the neighbour would let a caller fix every
        conflict it was shown and still be double-booked. Segments are sorted by
        arrival, so once a later segment starts on or after `earlier.depart` no
        segment after it can overlap `earlier` either — hence the break.
        """
        out: list[dict[str, Any]] = []
        ordered = self.committed(statuses)
        for i, earlier in enumerate(ordered):
            earlier_depart = _d(earlier.depart)
            for later in ordered[i + 1:]:
                if _d(later.arrive) >= earlier_depart:
                    break
                out.append({
                    "first": earlier.to_dict(),
                    "second": later.to_dict(),
                    # Clipped to the shorter segment so a fully-contained stay
                    # reports its own length, not the container's.
                    "overlap_days": (
                        min(earlier_depart, _d(later.depart)) - _d(later.arrive)
                    ).days,
                })
        return out

    def proposed_countries(self) -> tuple[str, ...]:
        """Countries this trip PROPOSES — its candidate segments, de-duplicated.

        The entry-eligibility gate reads this. Locked and planned segments are
        deliberately excluded: they are facts about a trip already committed to,
        and refusing to load a booked stay would help nobody.
        """
        return tuple(dict.fromkeys(
            s.country for s in self.segments if s.status == PROPOSAL_STATUS
        ))

    def out_of_window(self) -> list[dict[str, Any]]:
        """Segments falling wholly or partly outside the trip window."""
        ws, we = _d(self.window_start), _d(self.window_end)
        out: list[dict[str, Any]] = []
        for s in self.segments:
            if _d(s.arrive) < ws or _d(s.depart) > we:
                out.append(s.to_dict())
        return out

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_days": self.window_days,
            "scope": self.scope,
            "notes": self.notes,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Trip:
        return cls.build(
            slug=raw.get("slug"),
            title=raw.get("title") or raw.get("slug"),
            window_start=raw.get("window_start"),
            window_end=raw.get("window_end"),
            segments=raw.get("segments") or (),
            notes=raw.get("notes"),
            scope=raw.get("scope", "personal"),
            last_updated=raw.get("last_updated"),
        )


def _sorted(segments: tuple[Segment, ...]) -> tuple[Segment, ...]:
    return tuple(sorted(segments, key=lambda s: (s.arrive, s.depart, s.city)))


def compute_open_windows(window_start: str, window_end: str,
                         segments: tuple[Segment, ...] | list[Segment]) -> list[OpenWindow]:
    """Free stretches between committed segments. See the module docstring for the convention."""
    ws, we = _d(window_start), _d(window_end)
    if we < ws:
        raise V.ValidationError("window_end is before window_start")
    windows: list[OpenWindow] = []
    cursor = ws
    previous_label: str | None = None
    for seg in _sorted(tuple(segments)):
        arrive, depart = _d(seg.arrive), _d(seg.depart)
        if arrive > we:
            # Segment starts after the window closes; it cannot carve a window here.
            continue
        free_end = min(arrive - timedelta(days=1), we)
        if free_end >= cursor:
            windows.append(OpenWindow(
                index=len(windows) + 1,
                start=_iso(cursor),
                end=_iso(free_end),
                days=(free_end - cursor).days + 1,
                after=previous_label,
                before=seg.city,
            ))
        cursor = max(cursor, depart + timedelta(days=1))
        previous_label = seg.city
    if cursor <= we:
        windows.append(OpenWindow(
            index=len(windows) + 1,
            start=_iso(cursor),
            end=_iso(we),
            days=(we - cursor).days + 1,
            after=previous_label,
            before=None,
        ))
    return windows


# ------------------------------- persistence -------------------------------

def _relpath(slug: str) -> str:
    return f"{profile_mod.ITINERARIES_SUBDIR}/{slug}.md"


def render_body(trip: Trip) -> str:
    """Human-readable mirror of the frontmatter, for the vault UI."""
    lines = [
        f"# {trip.title}",
        "",
        f"Window: {trip.window_start} to {trip.window_end} ({trip.window_days} days) · scope: {trip.scope}",
        "",
        "| # | City | Country | Arrive | Depart | Nights | Status | Scope |",
        "|---|------|---------|--------|--------|--------|--------|-------|",
    ]
    for i, s in enumerate(trip.segments, start=1):
        lines.append(
            f"| {i} | {s.city} | {s.country} | {s.arrive} | {s.depart} | "
            f"{s.nights} | {s.status} | {s.scope} |"
        )
    if not trip.segments:
        lines.append("| - | (no segments yet) | | | | | | |")
    lines += ["", "## Open windows", ""]
    windows = trip.open_windows()
    if windows:
        for w in windows:
            edge = f" (before {w.before})" if w.before else " (to end of window)"
            lines.append(f"- {w.index}. {w.start} to {w.end} — {w.days} days{edge}")
    else:
        lines.append("- none: committed segments fill the window")
    if trip.notes:
        lines += ["", "## Notes", "", trip.notes]
    return "\n".join(lines) + "\n"


def save(trip: Trip) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "type": ITINERARY_TYPE,
        "slug": trip.slug,
        "title": trip.title,
        "window_start": trip.window_start,
        "window_end": trip.window_end,
        "scope": trip.scope,
        "segments": [s.to_dict() for s in trip.segments],
        "notes": trip.notes,
        "last_updated": time.strftime("%Y-%m-%d"),
    }
    return profile_mod.write_data_doc(_relpath(trip.slug), meta, render_body(trip))


def load(slug: str) -> Trip:
    safe = V.validate_slug(slug)
    doc = profile_mod.read_data_doc(_relpath(safe))
    if doc is None:
        raise FileNotFoundError(f"itinerary not found: {safe}")
    meta = dict(doc["frontmatter"])
    meta.setdefault("slug", safe)
    return Trip.from_dict(meta)


def list_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in profile_mod.list_data_docs(profile_mod.ITINERARIES_SUBDIR):
        meta = doc["frontmatter"]
        try:
            trip = Trip.from_dict({**meta, "slug": meta.get("slug") or doc["stem"]})
        except Exception:
            out.append({"slug": doc["stem"], "path": doc["path"], "readable": False})
            continue
        out.append({
            "slug": trip.slug,
            "title": trip.title,
            "window_start": trip.window_start,
            "window_end": trip.window_end,
            "segments": len(trip.segments),
            "open_windows": len(trip.open_windows()),
            "path": doc["path"],
            "readable": True,
        })
    return out
