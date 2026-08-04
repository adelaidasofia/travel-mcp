"""Vault read/write helpers for travel-mcp.

Layout under $TRAVEL_MCP_VAULT_PATH / $TRAVEL_MCP_PROFILE_FOLDER (default "🧳 Travel"):

  Profile.md              ← single-user master profile (frontmatter + body sections)
  Trips/<slug>.md         ← saved trip plans
  Companions/<name>.md    ← additional traveler profiles

All three are auto-created on first healthcheck if the vault path is set and the
folder doesn't exist. Profile.md is seeded with a template the user can fill in
via Obsidian or via update_travel_profile().
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import frontmatter

import audit

VAULT_ENV = "TRAVEL_MCP_VAULT_PATH"
FOLDER_ENV = "TRAVEL_MCP_PROFILE_FOLDER"
DEFAULT_FOLDER = "🧳 Travel"

#: Structured-data subfolders (itinerary model + prerequisite graphs).
ITINERARIES_SUBDIR = "Itineraries"
PREREQUISITES_SUBDIR = "Prerequisites"

PROFILE_TEMPLATE = """---
type: travel_profile
created: {created}
last_updated: {created}
---

# Travel Profile

## 1. Identity

- Legal name for bookings: [FILL IN]
- Date of birth: [FILL IN]
- Phone: [FILL IN]
- Email: [FILL IN]
- Passports held — NEVER record the document number here. Border eligibility
  needs the issuing country and the expiry date; nothing in this system reads
  the number, and a vault file is the wrong place to keep one.
  - Passport 1 — issuing country: [FILL IN] · expires: [YYYY-MM-DD]
  - Passport 2 — issuing country: [FILL IN OR NONE] · expires: [YYYY-MM-DD]
- Known Traveler Number (KTN / TSA PreCheck / Global Entry): [FILL IN]
- Redress Number: [FILL IN OR NONE]

## 2. Travel Style

Default: Efficient, comfortable, clean, low-stress.

Priority order:
1. Best schedule
2. Shortest travel time
3. Loyalty / status benefits
4. Comfort
5. Price
6. Points optimization

Do not optimize only for lowest price unless explicitly told 'cheapest possible'.

## 3. Credit Cards & Payment Strategy

Primary travel card: [CARD NAME] · last 4: [XXXX] · best for: [FLIGHTS / HOTELS / DINING / GENERAL] · benefits: [TRIP DELAY, LOUNGE, RENTAL INSURANCE]
Secondary card: [CARD NAME] · last 4: [XXXX] · best for: [CATEGORY]
Hotel card: [CARD NAME] · last 4: [XXXX] · benefits: [FREE NIGHTS, ELITE STATUS, UPGRADES]
Airline card: [CARD NAME] · last 4: [XXXX] · benefits: [FREE BAGS, PRIORITY BOARDING, LOUNGES]

Minimum redemption values (cents-per-point):
- Chase UR: 1.5 cpp
- Amex MR: 1.5 cpp
- Capital One: 1.3 cpp
- Airline miles: 1.3 cpm
- Hotel points: 0.7 cpp (adjust by program)

## 4. Airports & Flights

Primary airport: [IATA]
Backup airports: [IATA], [IATA]
Seat: Aisle > Window > Never middle
Prefer: Exit row, extra legroom, front half
Avoid: Last row, near bathrooms, basic economy

Cabin rules:
- Under 5 hours: Economy or premium economy
- 5+ hours: Premium economy or business
- Overnight: Business or premium economy
- Red-eyes: Avoid unless I explicitly approve
- Basic economy: Never book unless I approve

Schedule rules:
- Preferred departure: 7am to 11am
- Acceptable: 6am to 2pm
- Avoid: Before 6am, red-eyes, late arrivals
- Max connections: 1 stop
- Direct if under $200 more than best 1-stop
- Connection minutes: 60 domestic, 90 international

Preferred airlines (priority order):
1. [AIRLINE] — loyalty ID: [XXXX] — tier: [TIER]
2. [AIRLINE] — loyalty ID: [XXXX] — tier: [TIER]
3. [AIRLINE] — loyalty ID: [XXXX] — tier: [TIER]
Avoid: Frontier, Spirit, Allegiant

## 5. Hotels & Stays

Style: Clean, modern, safe, convenient.
Avoid: Sketchy areas, outdated rooms, bad wifi.

Must-haves: King bed, high floor (5th+), fast wifi, good gym, clean rooms, safe area, recent reviews, walking distance to plans.
Nice-to-haves: Breakfast, lounge, spa, pool, late checkout, upgrade potential, good lobby.

Hotel chains (priority order):
1. [CHAIN] — loyalty ID: [XXXX] — tier: [TIER]
2. [CHAIN] — loyalty ID: [XXXX] — tier: [TIER]
3. [CHAIN] — loyalty ID: [XXXX] — tier: [TIER]

Budget cap per night: $[NUMBER]. Can exceed by $50 if location/quality is worth it. Ask before exceeding beyond that.

## 6. Lounges & Airport Experience

TSA PreCheck: [YES/NO] · KTN: [XXXX]
Global Entry: [YES/NO]
CLEAR: [YES/NO]
Arrival buffer: 75 min domestic, 2.5h international
Lounge cards: [CARD] — access via [CENTURION / PRIORITY PASS / etc.]

## 7. Ground Transportation

Default: Uber Black after 9pm, regular Uber otherwise.
Rental company: [COMPANY] — loyalty ID: [XXXX]
Prefer: walking > Uber > transit > rental.

## 8. Restaurants

Favorite cuisines: [STEAK, SUSHI, ITALIAN, etc.]
Style: Fun, high-quality, not overly touristy.
Budget per dinner: $[NUMBER] per person.
Reservation platforms: [OPENTABLE / RESY / TOCK].

## 9. International Travel

Always check: passport validity, visa requirements, entry forms, vaccinations, local currency, outlet adapters, eSIM, tipping norms, ride-share apps, travel insurance.

## 10. Hard Booking Rules

1. NEVER book without my explicit approval.
2. Before booking, always show: recommended option + 1-2 alternatives, total cost with taxes/fees, cancellation policy, credit card to be charged, loyalty number used, points/miles earned, whether points or cash is better.
3. Wait for 'Go' or 'Book it' before proceeding.
4. Do NOT treat 'Looks good' or 'Interesting' as approval.
5. Flag tight cancellation windows, hidden fees, non-refundable policies before asking.
6. Never book without explicit approval: basic economy, non-refundable hotels, separate tickets, overnight layovers.

## 11. Voice

Direct, useful, efficient. Not overly enthusiastic. When there is a clear best option, say so. When something is risky or overpriced, tell me.
"""


def _vault_root() -> Path | None:
    raw = os.environ.get(VAULT_ENV)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


def _folder_name() -> str:
    return os.environ.get(FOLDER_ENV) or DEFAULT_FOLDER


def travel_root() -> Path | None:
    root = _vault_root()
    if root is None:
        return None
    return root / _folder_name()


def trips_dir() -> Path | None:
    tr = travel_root()
    return None if tr is None else tr / "Trips"


def companions_dir() -> Path | None:
    tr = travel_root()
    return None if tr is None else tr / "Companions"


def itineraries_dir() -> Path | None:
    tr = travel_root()
    return None if tr is None else tr / ITINERARIES_SUBDIR


def prerequisites_dir() -> Path | None:
    tr = travel_root()
    return None if tr is None else tr / PREREQUISITES_SUBDIR


def profile_path() -> Path | None:
    tr = travel_root()
    return None if tr is None else tr / "Profile.md"


def ensure_dirs() -> dict[str, Any]:
    """Create the travel folder tree + seed Profile.md if missing.

    Returns a status dict: {created: [...], existed: [...]}.
    """
    tr = travel_root()
    if tr is None:
        raise RuntimeError(
            f"{VAULT_ENV} is not set or path does not exist. "
            "Set TRAVEL_MCP_VAULT_PATH in admin.env or .mcp.json env block."
        )
    created, existed = [], []
    for d in (tr, trips_dir(), companions_dir(), itineraries_dir(), prerequisites_dir()):
        if d is None:
            continue
        if d.exists():
            existed.append(str(d))
        else:
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    pp = profile_path()
    if pp is not None and not pp.exists():
        now = time.strftime("%Y-%m-%d")
        pp.write_text(PROFILE_TEMPLATE.format(created=now), encoding="utf-8")
        created.append(str(pp))
    elif pp is not None:
        existed.append(str(pp))
    return {"created": created, "existed": existed}


# ---------------- profile ----------------

def read_profile() -> dict[str, Any]:
    pp = profile_path()
    if pp is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot read profile")
    if not pp.exists():
        ensure_dirs()
    post = frontmatter.load(str(pp))
    return {"frontmatter": dict(post.metadata), "body": post.content, "path": str(pp)}


def update_profile_section(section_heading: str, new_content: str) -> dict[str, Any]:
    """Replace the body of one `## Section` heading. Heading is matched as-is.

    The new_content goes BELOW the heading line. Adjacent sections are preserved.
    Creates the section at end-of-file if it doesn't exist.
    """
    guard_vault_write({"section_heading": section_heading}, new_content)
    # The whole file is rewritten, so a legacy number in an UNTOUCHED section
    # would be re-persisted by this guarded path. Remediate it here, the same
    # way upsert_companion remediates a legacy companion file.
    pp = profile_path()
    if pp is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot update profile")
    _remediated: list[str] = []
    if not pp.exists():
        ensure_dirs()
    post = frontmatter.load(str(pp))
    body = post.content
    # Match the section heading and the body until the next H2 or end-of-file.
    pattern = re.compile(
        rf"(^##\s+{re.escape(section_heading)}\s*\n)(.*?)(?=^##\s|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    if pattern.search(body):
        new_body = pattern.sub(rf"\1\n{new_content.rstrip()}\n\n", body)
    else:
        new_body = body.rstrip() + f"\n\n## {section_heading}\n\n{new_content.rstrip()}\n"
    # Remediate any legacy number in a section this call did not touch, and in
    # the frontmatter. Without this, the one multi-section file in the system is
    # the one place a pre-existing number survives a guarded write untouched.
    if audit.contains_labelled_document(new_body):
        new_body = audit.redact_labelled_documents(new_body)
        _remediated.append("body")
    for k in [k for k in list(post.metadata) if audit.is_document_number_key(k)]:
        post.metadata.pop(k, None)
        _remediated.append(k)

    post.content = new_body
    post.metadata["last_updated"] = time.strftime("%Y-%m-%d")
    with pp.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not new_body.endswith("\n"):
            f.write("\n")
    return {"path": str(pp), "section": section_heading, "bytes": pp.stat().st_size,
            "removed_identity_fields": _remediated}


# ---------------- companions ----------------

def _companion_path(name: str) -> Path:
    cd = companions_dir()
    if cd is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot resolve companions dir")
    safe = re.sub(r"[^\w\s\-]+", "", name).strip().replace(" ", "-")
    return cd / f"{safe}.md"


def list_companions() -> list[dict[str, Any]]:
    cd = companions_dir()
    if cd is None or not cd.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(cd.glob("*.md")):
        try:
            post = frontmatter.load(str(f))
            out.append({
                "name": post.metadata.get("name") or f.stem,
                "path": str(f),
                "frontmatter": dict(post.metadata),
            })
        except Exception:
            out.append({"name": f.stem, "path": str(f), "frontmatter": {}})
    return out


def read_companion(name: str) -> dict[str, Any]:
    p = _companion_path(name)
    if not p.exists():
        raise FileNotFoundError(f"companion not found: {p.name}")
    post = frontmatter.load(str(p))
    return {"name": post.metadata.get("name") or p.stem, "frontmatter": dict(post.metadata),
            "body": post.content, "path": str(p)}


class IdentityDocumentRejected(ValueError):
    """Raised when a caller tries to persist an identity-document number."""


def guard_vault_write(
    meta: dict[str, Any] | None = None,
    *texts: Any,
) -> None:
    """Fail loud before any identity-document number reaches the vault.

    Called by EVERY vault write primitive in this module. An earlier version
    guarded only `upsert_companion` while claiming in its own docstring to be
    "the write boundary itself" -- it was the boundary of one function out of
    four, and the other three were the sinks for six modules. A guard is only
    as wide as its call sites, never as wide as its docstring.

    A number arrives four ways, so the walk is recursive and covers all of them:
      1. a frontmatter KEY that names a document (`passport`, `cedula`, ...),
      2. a document number written into a frontmatter VALUE as prose,
      3. a document number written into free-text BODY content,
      4. the same, NESTED -- the been-there ledger stores `records: [{...}]`,
         so a scan of top-level values only leaves it sitting two levels down.

    Identifiers that become FILENAMES (`name`, `slug`, `relpath`) are passed in
    as text too: slugifying "Passport number: X" produced a file literally
    called `Passport-number-X.md`, a leak in the directory listing itself.

    Refuses document NUMBERS only. Date of birth and Known Traveler Number are
    withheld from the audit log but allowed through to the vault, which is the
    private store where booking flows legitimately need them.
    """
    violations = audit.find_document_violations(
        {"meta": meta or {}, "text": list(texts)}
    )
    if violations:
        raise IdentityDocumentRejected(
            f"refusing to write an identity-document number to the vault "
            f"({'; '.join(violations[:4])}). Border eligibility needs issuing "
            f"country and expiry date, never the document number. Use "
            f"passport_country / passport_expiry instead."
        )


#: Retained name for the companion path; the guard itself is now module-wide.
def reject_identity_documents(fields: dict[str, Any]) -> None:
    guard_vault_write(fields)


def upsert_companion(name: str, fields: dict[str, Any], body: str | None = None) -> dict[str, Any]:
    guard_vault_write(fields, body, name)
    p = _companion_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.load(str(p)) if p.exists() else frontmatter.Post(content=body or "")
    post.metadata["type"] = "travel_companion"
    post.metadata["name"] = name
    post.metadata.update({k: v for k, v in fields.items() if v is not None})
    # Remediate, do not merely refuse. A number written by the pre-fix version
    # (or typed into Obsidian by hand) is read off disk and would be rewritten
    # verbatim by this guarded path. Raising would leave such a file permanently
    # unwritable, so strip it here and report what was removed.
    stripped = [k for k in list(post.metadata)
                if audit.is_document_number_key(k)]
    for k in stripped:
        post.metadata.pop(k, None)
    # The BODY needs the same treatment. Reporting an empty list while a legacy
    # number still sits in the prose is worse than reporting nothing: a caller
    # reading removed_identity_fields gets an affirmative all-clear on a file
    # that still contains the number.
    if audit.contains_labelled_document(post.content):
        post.content = audit.redact_labelled_documents(post.content)
        stripped.append("body")
    post.metadata["last_updated"] = time.strftime("%Y-%m-%d")
    if body is not None:
        post.content = body
    with p.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not post.content.endswith("\n"):
            f.write("\n")
    return {"path": str(p), "name": name, "bytes": p.stat().st_size,
            "removed_identity_fields": stripped}


# ---------------- trip plans ----------------

def _trip_path(slug: str) -> Path:
    td = trips_dir()
    if td is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot resolve trips dir")
    return td / f"{slug}.md"


def save_trip(slug: str, summary: str, content: str,
              frontmatter_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    guard_vault_write(frontmatter_extra, content, summary, slug)
    p = _trip_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(content=content)
    post.metadata["type"] = "trip_plan"
    post.metadata["slug"] = slug
    post.metadata["summary"] = summary
    post.metadata["last_updated"] = time.strftime("%Y-%m-%d")
    if frontmatter_extra:
        post.metadata.update({k: v for k, v in frontmatter_extra.items() if v is not None})
    with p.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not content.endswith("\n"):
            f.write("\n")
    return {"path": str(p), "slug": slug, "bytes": p.stat().st_size}


def list_trips(year: int | None = None, destination_contains: str | None = None) -> list[dict[str, Any]]:
    td = trips_dir()
    if td is None or not td.exists():
        return []
    out: list[dict[str, Any]] = []
    needle = destination_contains.lower() if destination_contains else None
    for f in sorted(td.glob("*.md")):
        try:
            post = frontmatter.load(str(f))
            meta = dict(post.metadata)
            if year is not None:
                last = str(meta.get("last_updated", ""))
                if not last.startswith(str(year)):
                    continue
            if needle is not None:
                dest = str(meta.get("destination", "") or meta.get("summary", "")).lower()
                if needle not in dest:
                    continue
            out.append({
                "slug": meta.get("slug") or f.stem,
                "summary": meta.get("summary", ""),
                "destination": meta.get("destination"),
                "last_updated": meta.get("last_updated"),
                "path": str(f),
            })
        except Exception:
            out.append({"slug": f.stem, "path": str(f), "summary": ""})
    return out


def read_trip(slug: str) -> dict[str, Any]:
    p = _trip_path(slug)
    if not p.exists():
        raise FileNotFoundError(f"trip plan not found: {p.name}")
    post = frontmatter.load(str(p))
    return {"slug": slug, "frontmatter": dict(post.metadata), "body": post.content, "path": str(p)}


# ---------------- structured data docs ----------------
# Itineraries, been-there ledger, entry eligibility, preference profile v2 and
# prerequisite graphs are STRUCTURED. They live in frontmatter (round-trips as
# real data) with a rendered markdown body (readable in the vault UI), so the
# same file serves the model and the human.


def _data_doc_path(relpath: str) -> Path:
    """Resolve a path under the travel root, refusing anything that escapes it."""
    tr = travel_root()
    if tr is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot resolve travel folder")
    candidate = (tr / relpath).resolve()
    root = tr.resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError(f"path escapes the travel folder: {relpath!r}")
    return candidate


def read_data_doc(relpath: str) -> dict[str, Any] | None:
    """Return {frontmatter, body, path} or None when the file does not exist."""
    p = _data_doc_path(relpath)
    if not p.exists():
        return None
    post = frontmatter.load(str(p))
    return {"frontmatter": dict(post.metadata), "body": post.content, "path": str(p)}


def write_data_doc(relpath: str, meta: dict[str, Any], body: str) -> dict[str, Any]:
    guard_vault_write(meta, body, relpath)
    p = _data_doc_path(relpath)
    p.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(content=body)
    post.metadata.update(meta)
    with p.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not body.endswith("\n"):
            f.write("\n")
    return {"path": str(p), "bytes": p.stat().st_size}


def list_data_docs(subdir: str) -> list[dict[str, Any]]:
    tr = travel_root()
    if tr is None:
        return []
    d = _data_doc_path(subdir)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.md")):
        try:
            post = frontmatter.load(str(f))
            out.append({"stem": f.stem, "path": str(f), "frontmatter": dict(post.metadata)})
        except Exception:
            out.append({"stem": f.stem, "path": str(f), "frontmatter": {}})
    return out
