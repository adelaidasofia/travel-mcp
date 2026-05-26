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

VAULT_ENV = "TRAVEL_MCP_VAULT_PATH"
FOLDER_ENV = "TRAVEL_MCP_PROFILE_FOLDER"
DEFAULT_FOLDER = "🧳 Travel"

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
- Nationality / passport country: [FILL IN]
- Passport number: [FILL IN]
- Passport expiration: [FILL IN]
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
    for d in (tr, trips_dir(), companions_dir()):
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
    pp = profile_path()
    if pp is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot update profile")
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
    post.content = new_body
    post.metadata["last_updated"] = time.strftime("%Y-%m-%d")
    with pp.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not new_body.endswith("\n"):
            f.write("\n")
    return {"path": str(pp), "section": section_heading, "bytes": pp.stat().st_size}


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


def upsert_companion(name: str, fields: dict[str, Any], body: str | None = None) -> dict[str, Any]:
    p = _companion_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        post = frontmatter.load(str(p))
    else:
        post = frontmatter.Post(content=body or "")
    post.metadata["type"] = "travel_companion"
    post.metadata["name"] = name
    post.metadata.update({k: v for k, v in fields.items() if v is not None})
    post.metadata["last_updated"] = time.strftime("%Y-%m-%d")
    if body is not None:
        post.content = body
    with p.open("w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
        if not post.content.endswith("\n"):
            f.write("\n")
    return {"path": str(p), "name": name, "bytes": p.stat().st_size}


# ---------------- trip plans ----------------

def _trip_path(slug: str) -> Path:
    td = trips_dir()
    if td is None:
        raise RuntimeError(f"{VAULT_ENV} unset; cannot resolve trips dir")
    return td / f"{slug}.md"


def save_trip(slug: str, summary: str, content: str,
              frontmatter_extra: dict[str, Any] | None = None) -> dict[str, Any]:
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
