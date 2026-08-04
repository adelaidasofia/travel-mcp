"""Entry-eligibility store: three states, each carrying a source and an as-of date.

    verified-eligible     checked against a named source on a named date
    verified-ineligible   checked, and the answer was no
    unchecked             nobody has looked yet

Two rules that pull in opposite directions, both honoured:

  1. A router must NEVER PROPOSE a country that is not verified-eligible.
     `unchecked` is not a soft yes. `assert_proposable` raises; there is no
     variant that returns the country with a warning attached.

  2. A UI MAY DISPLAY an unchecked country, labelled unchecked. Hiding it would
     make an unknown look like a no. `display()` returns every state.

Verified states REQUIRE a source and an as-of timestamp — a verification whose
provenance was not recorded is not a verification, so construction refuses it.
Passing `max_age_days` re-reads that timestamp: a stale verification stops being
proposable without silently becoming a lie. An as-of date in the FUTURE is
refused at both ends (write and gate): it cannot have happened yet, and a
negative age would otherwise read as permanently fresh.

Rule 1 is enforced by `guard_proposed_countries`, which every proposing surface
calls. A gate that callers may opt into is not a gate.
"""

from __future__ import annotations

import profile as profile_mod
import time
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import validators as V

ELIGIBILITY_TYPE = "travel_entry_eligibility"
ELIGIBILITY_RELPATH = "Entry Eligibility.md"

PROPOSABLE_STATE = "verified-eligible"
VERIFIED_STATES = ("verified-eligible", "verified-ineligible")


class EligibilityRefused(RuntimeError):
    """Raised when a caller tries to propose a country that is not verified-eligible."""

    def __init__(self, country: str, state: str, reason: str) -> None:
        super().__init__(f"{country}: not proposable ({reason}; state={state})")
        self.country = country
        self.state = state
        self.reason = reason


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _assert_not_future(as_of: Any, *, now: str | None = None) -> None:
    """Refuse an as-of date that has not happened yet.

    A verification cannot have been performed after today, so a future as_of is
    a typo. Caught at the WRITE boundary, where it can still be corrected —
    `check_proposable` refuses it again at read time for anything already stored.
    """
    if as_of is None:
        return
    as_of_iso = V.coerce_iso_date(as_of, field="as_of")
    today = date.fromisoformat(V.coerce_iso_date(now or _today(), field="now"))
    if date.fromisoformat(as_of_iso) > today:
        raise V.ValidationError(
            f"as_of={as_of_iso} is in the future (today is {today.isoformat()}); "
            "a verification cannot have happened after today"
        )


@dataclass(frozen=True)
class EligibilityRecord:
    country: str
    state: str
    source: str | None = None
    as_of: str | None = None
    note: str | None = None

    @classmethod
    def build(cls, country: Any, state: Any, *, source: Any = None,
              as_of: Any = None, note: Any = None) -> EligibilityRecord:
        state_v = V.validate_eligibility_state(state)
        source_v = V.truncate(source, 200) if isinstance(source, str) and source.strip() else None
        as_of_v = V.coerce_iso_date(as_of, field="as_of") if as_of is not None else None
        if state_v in VERIFIED_STATES:
            if not source_v:
                raise V.ValidationError(
                    f"state={state_v} requires a source (who or what was checked)"
                )
            if not as_of_v:
                raise V.ValidationError(
                    f"state={state_v} requires an as_of date (when it was checked)"
                )
        return cls(
            country=V.normalize_country(country),
            state=state_v,
            source=source_v,
            as_of=as_of_v,
            note=(V.truncate(note, 400) if isinstance(note, str) and note.strip() else None),
        )

    @property
    def key(self) -> str:
        return self.country.casefold()

    @property
    def is_verified_eligible(self) -> bool:
        return self.state == PROPOSABLE_STATE

    def age_days(self, *, now: str | None = None) -> int | None:
        if not self.as_of:
            return None
        today = date.fromisoformat(V.coerce_iso_date(now or _today(), field="now"))
        return (today - date.fromisoformat(self.as_of)).days

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "state": self.state,
            "source": self.source,
            "as_of": self.as_of,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> EligibilityRecord:
        if not isinstance(raw, dict):
            raise V.ValidationError("each eligibility record must be an object")
        return cls.build(
            country=raw.get("country"),
            state=raw.get("state", "unchecked"),
            source=raw.get("source"),
            as_of=raw.get("as_of"),
            note=raw.get("note"),
        )


@dataclass(frozen=True)
class EligibilityStore:
    records: tuple[EligibilityRecord, ...] = ()

    # ---- reads ----

    def get(self, country: Any) -> EligibilityRecord:
        """Always returns a record. A country nobody checked reads as `unchecked`."""
        name = V.normalize_country(country)
        key = name.casefold()
        for r in self.records:
            if r.key == key:
                return r
        return EligibilityRecord(country=name, state="unchecked")

    def display(self, countries: Any = None) -> list[dict[str, Any]]:
        """Every state, including unchecked. This is the UI view, not the router view."""
        if countries:
            return [self.get(c).to_dict() for c in countries]
        return [r.to_dict() for r in self.records]

    # ---- the gate ----

    def check_proposable(self, country: Any, *, now: str | None = None,
                         max_age_days: int | None = None) -> tuple[bool, str, EligibilityRecord]:
        """(ok, reason, record). Reason is 'ok' only when the country may be proposed."""
        rec = self.get(country)
        if rec.state == "unchecked":
            return False, "unchecked", rec
        if rec.state == "verified-ineligible":
            return False, "verified-ineligible", rec
        age = rec.age_days(now=now)
        if age is not None and age < 0:
            # Checked UNCONDITIONALLY, not only when max_age_days is supplied.
            # A negative age means the record is dated after `now`, and the
            # staleness test below (age > max_age_days) can never fire on a
            # negative number — so a typo'd future date would read as
            # permanently fresh, which is the exact lie this module prevents.
            return False, "future-verification", rec
        if max_age_days is not None and age is not None and age > max_age_days:
            return False, "stale-verification", rec
        return True, "ok", rec

    def assert_proposable(self, country: Any, *, now: str | None = None,
                          max_age_days: int | None = None) -> EligibilityRecord:
        """Return the record, or raise. There is no third outcome."""
        ok, reason, rec = self.check_proposable(country, now=now, max_age_days=max_age_days)
        if not ok:
            raise EligibilityRefused(rec.country, rec.state, reason)
        return rec

    def filter_proposable(self, countries: Any, *, now: str | None = None,
                          max_age_days: int | None = None) -> dict[str, Any]:
        """Split a candidate list into what a router may propose and what it may not."""
        if isinstance(countries, str):
            countries = [countries]
        proposable: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for c in countries or []:
            ok, reason, rec = self.check_proposable(c, now=now, max_age_days=max_age_days)
            entry = rec.to_dict()
            if ok:
                proposable.append(entry)
            else:
                refused.append({**entry, "reason": reason})
        return {
            "proposable": proposable,
            "refused": refused,
            "proposable_count": len(proposable),
            "refused_count": len(refused),
        }

    # ---- writes ----

    def upsert(self, record: EligibilityRecord) -> EligibilityStore:
        kept = tuple(r for r in self.records if r.key != record.key)
        return replace(self, records=tuple(sorted((*kept, record), key=lambda r: r.key)))

    def set_state(self, country: Any, state: Any, *, source: Any = None,
                  as_of: Any = None, note: Any = None) -> EligibilityStore:
        if state is not None and V.validate_eligibility_state(state) in VERIFIED_STATES and as_of is None:
            as_of = _today()
        _assert_not_future(as_of)
        return self.upsert(EligibilityRecord.build(
            country, state, source=source, as_of=as_of, note=note,
        ))

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "count_records": len(self.records),
            "count_verified_eligible": sum(1 for r in self.records if r.is_verified_eligible),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> EligibilityStore:
        rows = (raw or {}).get("records") or []
        if isinstance(rows, dict):
            raise V.ValidationError("eligibility records must be a list, not an object")
        built = tuple(EligibilityRecord.from_dict(r) for r in rows)
        return cls(records=tuple(sorted(built, key=lambda r: r.key)))


# --------------------------------- the gate ---------------------------------

def guard_proposed_countries(countries: Any, *, store: EligibilityStore | None = None,
                             now: str | None = None,
                             max_age_days: int | None = None) -> tuple[EligibilityRecord, ...]:
    """CHOKEPOINT. Raise on the first country that may not be proposed.

    Every surface that PROPOSES a country calls this: a candidate segment on the
    way in, and any planning output that counts candidates on the way out. It is
    deliberately not opt-in — a gate only one caller remembers to invoke is not a
    gate, which is the bug this function exists to close.

    The policy is `check_proposable`'s, unchanged: only `verified-eligible`
    passes, `unchecked` is refused because an unknown is not a soft yes, and a
    verification dated in the future or past `max_age_days` is refused too.

    DISPLAY paths never call this. `get_entry_eligibility` and `get_itinerary`
    keep rendering every state, labelled — hiding an unknown would make it look
    like a no, and a stay already booked is a fact, not a proposal.

    Loads the store once for the whole list; pass `store` to reuse an open one.
    """
    if isinstance(countries, str):
        countries = [countries]
    countries = list(countries or [])
    if not countries:
        return ()
    active = store if store is not None else load()
    checked: list[EligibilityRecord] = []
    for c in countries:
        rec = active.assert_proposable(c, now=now, max_age_days=max_age_days)
        checked.append(rec)
    return tuple(checked)


# ------------------------------- persistence -------------------------------

def render_body(store: EligibilityStore) -> str:
    lines = [
        "# Entry eligibility",
        "",
        "A router proposes only `verified-eligible`. `unchecked` renders as unchecked;",
        "it is not a soft yes and it is not a no.",
        "",
        "| Country | State | As of | Source |",
        "|---------|-------|-------|--------|",
    ]
    for r in store.records:
        lines.append(f"| {r.country} | {r.state} | {r.as_of or ''} | {r.source or ''} |")
    if not store.records:
        lines.append("| (empty) | | | |")
    return "\n".join(lines) + "\n"


def load() -> EligibilityStore:
    doc = profile_mod.read_data_doc(ELIGIBILITY_RELPATH)
    if doc is None:
        return EligibilityStore()
    return EligibilityStore.from_dict(dict(doc["frontmatter"]))


def save(store: EligibilityStore) -> dict[str, Any]:
    meta = {
        "type": ELIGIBILITY_TYPE,
        "records": [r.to_dict() for r in store.records],
        "last_updated": _today(),
    }
    return profile_mod.write_data_doc(ELIGIBILITY_RELPATH, meta, render_body(store))
