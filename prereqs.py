"""Prerequisite graph, scheduled BACKWARD from departure.

A visa that takes six weeks is not a to-do item, it is a deadline that has
already started moving. Every prerequisite carries:

    lead_days          how long the process takes once started
    hard_cutoff_days   it must be COMPLETE at least this many days before
                       departure (a vaccine that needs 10 days to take effect
                       is not "done" the day you fly)
    depends_on         keys that must COMPLETE before this one can START

Scheduling runs backward from the departure date:

    complete_by = min(departure - hard_cutoff_days,
                      earliest start_by among everything that depends on this)
    start_by    = complete_by - lead_days

Dependents therefore have to be solved before their dependencies, which is
reverse topological order. A dependency cycle raises instead of looping, an
unknown dependency key raises instead of being skipped, and two entries under
one key raise a duplicate-key error naming the key: a prerequisite silently
dropped from a schedule is the failure this module exists to prevent.
"""

from __future__ import annotations

import profile as profile_mod
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import validators as V

PREREQ_TYPE = "travel_prerequisites"

DEFAULT_WARN_DAYS = 7


class PrerequisiteCycleError(ValueError):
    """depends_on forms a cycle; no backward schedule exists."""


class UnknownPrerequisiteError(ValueError):
    """depends_on names a key that is not in the graph."""


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


@dataclass(frozen=True)
class Prerequisite:
    key: str
    kind: str
    label: str
    lead_days: int
    hard_cutoff_days: int = 0
    country: str | None = None
    depends_on: tuple[str, ...] = ()

    @classmethod
    def build(cls, key: Any, kind: Any, label: Any = None, lead_days: Any = 0,
              hard_cutoff_days: Any = 0, country: Any = None,
              depends_on: Any = ()) -> Prerequisite:
        deps = depends_on or ()
        if isinstance(deps, str):
            deps = [deps]
        key_v = V.validate_key(key, field="prerequisite key")
        dep_keys = tuple(dict.fromkeys(V.validate_key(d, field="depends_on") for d in deps))
        if key_v in dep_keys:
            raise PrerequisiteCycleError(f"{key_v} depends on itself")
        return cls(
            key=key_v,
            kind=V.validate_prerequisite_kind(kind),
            label=V.validate_name(label or key_v, field="label", limit=200),
            lead_days=V.validate_non_negative_int(lead_days, field="lead_days", maximum=3650),
            hard_cutoff_days=V.validate_non_negative_int(
                hard_cutoff_days, field="hard_cutoff_days", maximum=3650,
            ),
            country=(V.normalize_country(country) if country else None),
            depends_on=dep_keys,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "lead_days": self.lead_days,
            "hard_cutoff_days": self.hard_cutoff_days,
            "country": self.country,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Prerequisite:
        if not isinstance(raw, dict):
            raise V.ValidationError("each prerequisite must be an object")
        return cls.build(
            key=raw.get("key"),
            kind=raw.get("kind", "document"),
            label=raw.get("label"),
            lead_days=raw.get("lead_days", 0),
            hard_cutoff_days=raw.get("hard_cutoff_days", 0),
            country=raw.get("country"),
            depends_on=raw.get("depends_on") or (),
        )


@dataclass(frozen=True)
class ScheduledPrerequisite:
    key: str
    kind: str
    label: str
    country: str | None
    lead_days: int
    hard_cutoff_days: int
    start_by: str
    complete_by: str
    status: str
    slack_days: int | None
    depends_on: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "country": self.country,
            "lead_days": self.lead_days,
            "hard_cutoff_days": self.hard_cutoff_days,
            "start_by": self.start_by,
            "complete_by": self.complete_by,
            "status": self.status,
            "slack_days": self.slack_days,
            "depends_on": list(self.depends_on),
        }


def _assert_unique_keys(prereqs: tuple[Prerequisite, ...]) -> None:
    """Two entries under one key is a caller mistake; say so by name.

    Runs BEFORE the topological sort. `_reverse_topological` keys its graph by
    `key`, so a duplicate silently collapses two entries into one and the length
    check at the end mismatches — reporting `dependency cycle among: []`, a cycle
    error naming nothing stuck. Catching it here keeps the message true.
    """
    keys = [p.key for p in prereqs]
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise V.ValidationError(f"duplicate prerequisite keys: {dupes}")


def _reverse_topological(prereqs: tuple[Prerequisite, ...]) -> list[Prerequisite]:
    """Dependents first. Raises on a duplicate key, a cycle or an unknown key."""
    _assert_unique_keys(prereqs)
    by_key = {p.key: p for p in prereqs}
    for p in prereqs:
        for dep in p.depends_on:
            if dep not in by_key:
                raise UnknownPrerequisiteError(
                    f"{p.key} depends on unknown prerequisite {dep!r}"
                )
    # Kahn over edges dependency -> dependent, then reverse the result.
    indegree = {p.key: len(p.depends_on) for p in prereqs}
    dependents: dict[str, list[str]] = {p.key: [] for p in prereqs}
    for p in prereqs:
        for dep in p.depends_on:
            dependents[dep].append(p.key)
    ready = sorted(k for k, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while ready:
        key = ready.pop(0)
        order.append(key)
        for nxt in sorted(dependents[key]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    if len(order) != len(prereqs):
        stuck = sorted(set(by_key) - set(order))
        raise PrerequisiteCycleError(f"dependency cycle among: {stuck}")
    return [by_key[k] for k in reversed(order)]


def schedule_backward(prereqs: Any, departure: Any, *, today: Any = None,
                      warn_days: Any = DEFAULT_WARN_DAYS) -> list[ScheduledPrerequisite]:
    """Latest start/complete dates per prerequisite, sorted by urgency."""
    built = tuple(
        p if isinstance(p, Prerequisite) else Prerequisite.from_dict(p)
        for p in (prereqs or ())
    )
    _assert_unique_keys(built)
    departure_iso = V.coerce_iso_date(departure, field="departure")
    warn = V.validate_non_negative_int(warn_days, field="warn_days", maximum=365)
    today_iso = V.coerce_iso_date(today, field="today") if today is not None else time.strftime("%Y-%m-%d")
    departure_d, today_d = _d(departure_iso), _d(today_iso)

    start_by: dict[str, date] = {}
    complete_by: dict[str, date] = {}
    dependents: dict[str, list[str]] = {p.key: [] for p in built}
    for p in built:
        for dep in p.depends_on:
            if dep in dependents:
                dependents[dep].append(p.key)

    for p in _reverse_topological(built):
        latest = departure_d - timedelta(days=p.hard_cutoff_days)
        for child in dependents[p.key]:
            # Everything downstream must be able to start after this completes.
            latest = min(latest, start_by[child])
        complete_by[p.key] = latest
        start_by[p.key] = latest - timedelta(days=p.lead_days)

    out: list[ScheduledPrerequisite] = []
    for p in built:
        start = start_by[p.key]
        slack = (start - today_d).days
        if slack < 0:
            status = "overdue"
        elif slack <= warn:
            status = "due-soon"
        else:
            status = "on-track"
        out.append(ScheduledPrerequisite(
            key=p.key,
            kind=p.kind,
            label=p.label,
            country=p.country,
            lead_days=p.lead_days,
            hard_cutoff_days=p.hard_cutoff_days,
            start_by=start.isoformat(),
            complete_by=complete_by[p.key].isoformat(),
            status=status,
            slack_days=slack,
            depends_on=p.depends_on,
        ))
    out.sort(key=lambda s: (s.start_by, s.key))
    return out


def summarize(scheduled: list[ScheduledPrerequisite], *, departure: str) -> dict[str, Any]:
    return {
        "departure": departure,
        "count": len(scheduled),
        "overdue": [s.key for s in scheduled if s.status == "overdue"],
        "due_soon": [s.key for s in scheduled if s.status == "due-soon"],
        "earliest_start_by": scheduled[0].start_by if scheduled else None,
        "prerequisites": [s.to_dict() for s in scheduled],
    }


# ------------------------------- persistence -------------------------------

def _relpath(trip_slug: str) -> str:
    return f"{profile_mod.PREREQUISITES_SUBDIR}/{trip_slug}.md"


def render_body(trip_slug: str, prereqs: tuple[Prerequisite, ...]) -> str:
    lines = [
        f"# Prerequisites — {trip_slug}",
        "",
        "Scheduled backward from departure. `complete_by` respects both the hard",
        "cutoff and anything downstream that cannot start until this is done.",
        "",
        "| Key | Kind | Lead days | Hard cutoff | Depends on |",
        "|-----|------|-----------|-------------|------------|",
    ]
    for p in prereqs:
        lines.append(
            f"| {p.key} | {p.kind} | {p.lead_days} | {p.hard_cutoff_days} | "
            f"{', '.join(p.depends_on) or '-'} |"
        )
    if not prereqs:
        lines.append("| (empty) | | | | |")
    return "\n".join(lines) + "\n"


def save(trip_slug: str, prereqs: Any) -> dict[str, Any]:
    slug = V.validate_slug(trip_slug)
    built = tuple(
        p if isinstance(p, Prerequisite) else Prerequisite.from_dict(p)
        for p in (prereqs or ())
    )
    # Fail at write time, not at read time: an unschedulable graph never lands.
    _reverse_topological(built)
    meta = {
        "type": PREREQ_TYPE,
        "trip_slug": slug,
        "prerequisites": [p.to_dict() for p in built],
        "last_updated": time.strftime("%Y-%m-%d"),
    }
    return profile_mod.write_data_doc(_relpath(slug), meta, render_body(slug, built))


def load(trip_slug: str) -> tuple[Prerequisite, ...]:
    slug = V.validate_slug(trip_slug)
    doc = profile_mod.read_data_doc(_relpath(slug))
    if doc is None:
        raise FileNotFoundError(f"no prerequisites saved for trip: {slug}")
    rows = doc["frontmatter"].get("prerequisites") or []
    if isinstance(rows, dict):
        raise V.ValidationError("prerequisites must be a list, not an object")
    return tuple(Prerequisite.from_dict(r) for r in rows)
