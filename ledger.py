"""Been-there ledger with per-country provenance.

THE RULE, enforced here rather than documented elsewhere:

    A visit counts ONLY when the traveler affirmed it. An inference the
    traveler did not confirm counts as NOT VISITED, and is committed as such
    immediately. It is never left pending and never counted as a visit.

There is deliberately no `pending` provenance and no deferred-write path. An
inference that arrives without confirmation is written to disk as
`visited: false, provenance: inferred` in the same call. The four states:

    confirmed   traveler affirmed the visit            -> visited = True
    asked       traveler was asked and denied it       -> visited = False
    inferred    system guessed, no confirmation        -> visited = False
    unknown     no signal at all                       -> visited = False

`claim` preserves what was inferred so the guess is not lost, but it is a
separate field from `visited` and no count ever reads it. The rule is re-applied
on LOAD as well as on write, so a hand-edited file claiming
`visited: true, provenance: inferred` is corrected on read instead of trusted.
"""

from __future__ import annotations

import profile as profile_mod
import time
from dataclasses import dataclass, replace
from typing import Any

import validators as V

LEDGER_TYPE = "travel_been_there_ledger"
LEDGER_RELPATH = "Been There.md"

#: The only provenance under which a visit counts.
AFFIRMING_PROVENANCE = "confirmed"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def applies_visited(claim: Any, provenance: str) -> bool:
    """THE rule: visited is true only for an affirmed claim under `confirmed`."""
    return bool(claim) and provenance == AFFIRMING_PROVENANCE


@dataclass(frozen=True)
class CountryRecord:
    country: str
    visited: bool
    provenance: str
    claim: bool
    source: str | None = None
    as_of: str | None = None
    note: str | None = None

    @classmethod
    def build(cls, country: Any, *, provenance: Any, claim: Any = False,
              source: Any = None, as_of: Any = None, note: Any = None) -> CountryRecord:
        prov = V.validate_provenance(provenance)
        claim_bool = bool(claim)
        return cls(
            country=V.normalize_country(country),
            visited=applies_visited(claim_bool, prov),
            provenance=prov,
            claim=claim_bool,
            source=(V.truncate(source, 200) if isinstance(source, str) and source.strip() else None),
            as_of=(as_of if isinstance(as_of, str) and as_of.strip() else _now()),
            note=(V.truncate(note, 400) if isinstance(note, str) and note.strip() else None),
        )

    @property
    def key(self) -> str:
        return self.country.casefold()

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "visited": self.visited,
            "provenance": self.provenance,
            "claim": self.claim,
            "source": self.source,
            "as_of": self.as_of,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CountryRecord:
        if not isinstance(raw, dict):
            raise V.ValidationError("each ledger record must be an object")
        # `claim` falls back to the stored `visited` so a file written by an
        # older writer keeps its meaning, then the rule is re-applied below.
        claim = raw.get("claim")
        if claim is None:
            claim = raw.get("visited", False)
        return cls.build(
            country=raw.get("country"),
            provenance=raw.get("provenance", "unknown"),
            claim=claim,
            source=raw.get("source"),
            as_of=raw.get("as_of"),
            note=raw.get("note"),
        )


@dataclass(frozen=True)
class BeenThereLedger:
    records: tuple[CountryRecord, ...] = ()

    # ---- reads ----

    def get(self, country: Any) -> CountryRecord | None:
        key = V.country_key(country)
        for r in self.records:
            if r.key == key:
                return r
        return None

    def is_visited(self, country: Any) -> bool:
        rec = self.get(country)
        return bool(rec and rec.visited)

    def visited_countries(self) -> tuple[str, ...]:
        return tuple(sorted(r.country for r in self.records if r.visited))

    def count_visited(self) -> int:
        return sum(1 for r in self.records if r.visited)

    def unconfirmed(self) -> tuple[CountryRecord, ...]:
        """Committed-but-not-affirmed rows: the queue of questions worth asking."""
        return tuple(r for r in self.records if r.provenance == "inferred")

    # ---- writes (every one commits; none defer) ----

    def upsert(self, record: CountryRecord) -> BeenThereLedger:
        kept = tuple(r for r in self.records if r.key != record.key)
        return replace(self, records=tuple(sorted((*kept, record), key=lambda r: r.key)))

    def record_inference(self, country: Any, *, claim: bool = True, source: Any = None,
                         note: Any = None, as_of: Any = None) -> BeenThereLedger:
        """Commit an UNCONFIRMED inference. Result: visited=False, provenance=inferred.

        This is the whole point of the rule — the row is written now, counted as
        not-visited, and can be upgraded later by `confirm`.
        """
        return self.upsert(CountryRecord.build(
            country, provenance="inferred", claim=claim, source=source, note=note, as_of=as_of,
        ))

    def confirm(self, country: Any, *, visited: bool, source: Any = None,
                note: Any = None, as_of: Any = None) -> BeenThereLedger:
        """Traveler answered. True -> `confirmed` (counts). False -> `asked` (does not)."""
        provenance = AFFIRMING_PROVENANCE if visited else "asked"
        return self.upsert(CountryRecord.build(
            country, provenance=provenance, claim=bool(visited),
            source=source, note=note, as_of=as_of,
        ))

    def mark_unknown(self, country: Any, *, note: Any = None) -> BeenThereLedger:
        return self.upsert(CountryRecord.build(
            country, provenance="unknown", claim=False, note=note,
        ))

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "count_visited": self.count_visited(),
            "count_records": len(self.records),
            "unconfirmed": [r.country for r in self.unconfirmed()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BeenThereLedger:
        rows = (raw or {}).get("records") or []
        if isinstance(rows, dict):
            raise V.ValidationError("ledger records must be a list, not an object")
        built = tuple(CountryRecord.from_dict(r) for r in rows)
        return cls(records=tuple(sorted(built, key=lambda r: r.key)))


# ------------------------------- persistence -------------------------------

def render_body(led: BeenThereLedger) -> str:
    lines = [
        "# Been-there ledger",
        "",
        f"Visited (confirmed): {led.count_visited()} · rows: {len(led.records)}",
        "",
        "A row counts as visited only when provenance is `confirmed`. Unconfirmed",
        "inferences are stored as not-visited, never as pending.",
        "",
        "| Country | Visited | Provenance | As of | Source |",
        "|---------|---------|------------|-------|--------|",
    ]
    for r in led.records:
        lines.append(
            f"| {r.country} | {'yes' if r.visited else 'no'} | {r.provenance} | "
            f"{r.as_of or ''} | {r.source or ''} |"
        )
    if not led.records:
        lines.append("| (empty) | | | | |")
    return "\n".join(lines) + "\n"


def load() -> BeenThereLedger:
    doc = profile_mod.read_data_doc(LEDGER_RELPATH)
    if doc is None:
        return BeenThereLedger()
    return BeenThereLedger.from_dict(dict(doc["frontmatter"]))


def save(led: BeenThereLedger) -> dict[str, Any]:
    meta = {
        "type": LEDGER_TYPE,
        "records": [r.to_dict() for r in led.records],
        "count_visited": led.count_visited(),
        "last_updated": time.strftime("%Y-%m-%d"),
    }
    return profile_mod.write_data_doc(LEDGER_RELPATH, meta, render_body(led))
