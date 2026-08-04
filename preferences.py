"""Preference profile v2.

Three things v1 could not express:

  1. A place carries MULTIPLE relationships at once, never one. Somewhere can be
     for-a-person AND been-done AND love-the-place simultaneously, and a model
     that forces a single label loses exactly the nuance that decides the trip.

  2. Novelty and depth are INDEPENDENT dials. Wanting new places and wanting
     them properly is not a contradiction, so nothing here normalizes the pair,
     trades one against the other, or derives one from the other. Both may be
     1.0. Both may be 0.0.

  3. Standing seeds apply BY REFERENCE. A place stores `seed_refs`, never a copy
     of the seed's values, so editing a seed updates every place that points at
     it. `resolve_place` reads the seed at call time and surfaces dangling refs
     instead of silently dropping them.

Time budget convention, stated once:
  `discretionary_hours_per_day` is the free time on a NON-work day. On a work
  day the work block is subtracted from it (floored at zero).
"""

from __future__ import annotations

import profile as profile_mod
import time
from dataclasses import dataclass, replace
from typing import Any

import validators as V

PREFERENCES_TYPE = "travel_preference_profile"
PREFERENCES_RELPATH = "Preferences.md"
PROFILE_VERSION = 2

DEFAULT_DISCRETIONARY_HOURS = 8.0


@dataclass(frozen=True)
class Dials:
    """Two independent appetites. Neither constrains the other."""

    novelty: float = 0.5
    depth: float = 0.5

    @classmethod
    def build(cls, novelty: Any = 0.5, depth: Any = 0.5) -> Dials:
        return cls(
            novelty=V.validate_unit_interval(novelty, field="novelty"),
            depth=V.validate_unit_interval(depth, field="depth"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"novelty": self.novelty, "depth": self.depth, "independent": True}


@dataclass(frozen=True)
class WorkBlock:
    start: str
    end: str
    days: tuple[str, ...] = ()

    @classmethod
    def build(cls, start: Any, end: Any, days: Any = ()) -> WorkBlock:
        start_v = V.validate_hhmm(start, field="work_start")
        end_v = V.validate_hhmm(end, field="work_end")
        if _minutes(end_v) <= _minutes(start_v):
            raise V.ValidationError("work_end must be after work_start")
        return cls(start=start_v, end=end_v, days=V.validate_weekdays(days or ()))

    @property
    def hours(self) -> float:
        return (_minutes(self.end) - _minutes(self.start)) / 60.0

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "days": list(self.days), "hours": self.hours}


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class TimeBudget:
    discretionary_hours_per_day: float = DEFAULT_DISCRETIONARY_HOURS
    work_block: WorkBlock | None = None

    @classmethod
    def build(cls, discretionary_hours_per_day: Any = DEFAULT_DISCRETIONARY_HOURS,
              work_block: Any = None) -> TimeBudget:
        block = None
        if isinstance(work_block, WorkBlock):
            block = work_block
        elif isinstance(work_block, dict):
            block = WorkBlock.build(
                work_block.get("start"), work_block.get("end"), work_block.get("days", ()),
            )
        return cls(
            discretionary_hours_per_day=V.validate_positive_number(
                discretionary_hours_per_day, field="discretionary_hours_per_day", maximum=24,
            ),
            work_block=block,
        )

    def effective_discretionary_hours(self, *, work_day: bool = False) -> float:
        """Free hours. On a work day the work block comes out of the budget."""
        if not work_day or self.work_block is None:
            return self.discretionary_hours_per_day
        return max(0.0, self.discretionary_hours_per_day - self.work_block.hours)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discretionary_hours_per_day": self.discretionary_hours_per_day,
            "work_block": self.work_block.to_dict() if self.work_block else None,
            "effective_hours_work_day": self.effective_discretionary_hours(work_day=True),
            "effective_hours_free_day": self.effective_discretionary_hours(work_day=False),
        }


@dataclass(frozen=True)
class Seed:
    """A standing preference other records point at. Never copied into them."""

    key: str
    label: str
    values: dict[str, Any]

    @classmethod
    def build(cls, key: Any, label: Any = None, values: Any = None) -> Seed:
        key_v = V.validate_key(key, field="seed key")
        if values is not None and not isinstance(values, dict):
            raise V.ValidationError("seed values must be an object")
        return cls(
            key=key_v,
            label=V.validate_name(label or key_v, field="seed label", limit=200),
            values=dict(values or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "values": dict(self.values)}


@dataclass(frozen=True)
class PlacePreference:
    place: str
    relationships: tuple[str, ...]
    country: str | None = None
    seed_refs: tuple[str, ...] = ()
    note: str | None = None

    @classmethod
    def build(cls, place: Any, relationships: Any, country: Any = None,
              seed_refs: Any = (), note: Any = None) -> PlacePreference:
        refs = seed_refs or ()
        if isinstance(refs, str):
            refs = [refs]
        return cls(
            place=V.validate_name(place, field="place"),
            relationships=V.validate_relationships(relationships),
            country=(V.normalize_country(country) if country else None),
            seed_refs=tuple(dict.fromkeys(V.validate_key(r, field="seed_ref") for r in refs)),
            note=(V.truncate(note, 400) if isinstance(note, str) and note.strip() else None),
        )

    @property
    def key(self) -> str:
        return self.place.casefold()

    def to_dict(self) -> dict[str, Any]:
        return {
            "place": self.place,
            "country": self.country,
            "relationships": list(self.relationships),
            "seed_refs": list(self.seed_refs),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> PlacePreference:
        if not isinstance(raw, dict):
            raise V.ValidationError("each place preference must be an object")
        return cls.build(
            place=raw.get("place"),
            relationships=raw.get("relationships") or [],
            country=raw.get("country"),
            seed_refs=raw.get("seed_refs") or (),
            note=raw.get("note"),
        )


@dataclass(frozen=True)
class PreferenceProfile:
    version: int = PROFILE_VERSION
    dials: Dials = Dials()
    time_budget: TimeBudget = TimeBudget()
    places: tuple[PlacePreference, ...] = ()
    seeds: tuple[Seed, ...] = ()

    # ---- reads ----

    def seed(self, key: Any) -> Seed | None:
        key_v = V.validate_key(key, field="seed key")
        for s in self.seeds:
            if s.key == key_v:
                return s
        return None

    def place(self, name: Any) -> PlacePreference | None:
        key = V.validate_name(name, field="place").casefold()
        for p in self.places:
            if p.key == key:
                return p
        return None

    def places_with(self, relationship: Any) -> tuple[PlacePreference, ...]:
        rel = V.validate_relationships([relationship])[0]
        return tuple(p for p in self.places if rel in p.relationships)

    def resolve_place(self, name: Any) -> dict[str, Any]:
        """Resolve seeds AT READ TIME so an edited seed reaches every place at once."""
        pref = self.place(name)
        if pref is None:
            raise KeyError(f"no place preference for {name!r}")
        resolved, dangling = [], []
        for ref in pref.seed_refs:
            found = self.seed(ref)
            if found is None:
                dangling.append(ref)
            else:
                resolved.append(found.to_dict())
        return {
            **pref.to_dict(),
            "resolved_seeds": resolved,
            "dangling_seed_refs": dangling,
        }

    # ---- writes ----

    def with_dials(self, novelty: Any, depth: Any) -> PreferenceProfile:
        return replace(self, dials=Dials.build(novelty, depth))

    def with_time_budget(self, discretionary_hours_per_day: Any,
                         work_block: Any = None) -> PreferenceProfile:
        return replace(self, time_budget=TimeBudget.build(discretionary_hours_per_day, work_block))

    def upsert_place(self, pref: PlacePreference) -> PreferenceProfile:
        kept = tuple(p for p in self.places if p.key != pref.key)
        return replace(self, places=tuple(sorted((*kept, pref), key=lambda p: p.key)))

    def upsert_seed(self, seed: Seed) -> PreferenceProfile:
        kept = tuple(s for s in self.seeds if s.key != seed.key)
        return replace(self, seeds=tuple(sorted((*kept, seed), key=lambda s: s.key)))

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "dials": self.dials.to_dict(),
            "time_budget": self.time_budget.to_dict(),
            "places": [p.to_dict() for p in self.places],
            "seeds": [s.to_dict() for s in self.seeds],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> PreferenceProfile:
        data = raw or {}
        dials_raw = data.get("dials") or {}
        budget_raw = data.get("time_budget") or {}
        places_raw = data.get("places") or []
        seeds_raw = data.get("seeds") or []
        if isinstance(places_raw, dict) or isinstance(seeds_raw, dict):
            raise V.ValidationError("places and seeds must be lists, not objects")
        return cls(
            version=PROFILE_VERSION,
            dials=Dials.build(dials_raw.get("novelty", 0.5), dials_raw.get("depth", 0.5)),
            time_budget=TimeBudget.build(
                budget_raw.get("discretionary_hours_per_day", DEFAULT_DISCRETIONARY_HOURS),
                budget_raw.get("work_block"),
            ),
            places=tuple(sorted(
                (PlacePreference.from_dict(p) for p in places_raw), key=lambda p: p.key,
            )),
            seeds=tuple(sorted(
                (Seed.build(s.get("key"), s.get("label"), s.get("values")) for s in seeds_raw),
                key=lambda s: s.key,
            )),
        )


# ------------------------------- persistence -------------------------------

def render_body(prof: PreferenceProfile) -> str:
    lines = [
        "# Travel preferences (v2)",
        "",
        f"Novelty {prof.dials.novelty} · depth {prof.dials.depth} "
        "(independent dials — neither constrains the other)",
        f"Discretionary hours per day: {prof.time_budget.discretionary_hours_per_day}",
    ]
    if prof.time_budget.work_block:
        wb = prof.time_budget.work_block
        days = ", ".join(wb.days) if wb.days else "any day"
        lines.append(f"Work block: {wb.start}-{wb.end} ({wb.hours}h) on {days}")
    lines += [
        "",
        "## Places",
        "",
        "| Place | Country | Relationships | Seeds |",
        "|-------|---------|---------------|-------|",
    ]
    for p in prof.places:
        lines.append(
            f"| {p.place} | {p.country or ''} | {', '.join(p.relationships)} | "
            f"{', '.join(p.seed_refs)} |"
        )
    if not prof.places:
        lines.append("| (empty) | | | |")
    lines += ["", "## Standing seeds", ""]
    if prof.seeds:
        lines += [f"- `{s.key}` — {s.label}" for s in prof.seeds]
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def load() -> PreferenceProfile:
    doc = profile_mod.read_data_doc(PREFERENCES_RELPATH)
    if doc is None:
        return PreferenceProfile()
    return PreferenceProfile.from_dict(dict(doc["frontmatter"]))


def save(prof: PreferenceProfile) -> dict[str, Any]:
    meta = {
        "type": PREFERENCES_TYPE,
        **prof.to_dict(),
        "last_updated": time.strftime("%Y-%m-%d"),
    }
    return profile_mod.write_data_doc(PREFERENCES_RELPATH, meta, render_body(prof))
