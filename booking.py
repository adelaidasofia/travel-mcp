"""Booking link construction.

Geo reasons about fares and channels; this module is what turns that reasoning
into somewhere to click. Every URL here is BUILT BY CODE from validated inputs.
No URL is ever produced by the model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import validators as V


@dataclass(frozen=True)
class Channel:
    """One place a trip can be booked, and how to build its search URL.

    ``verified`` is the gate. These URL formats are undocumented -- no operator
    publishes a deep-link contract -- so the only honest basis for emitting one
    is having opened it and watched the right trip come back. ``evidence``
    records that observation. An unverified channel is withheld, never guessed.
    """

    key: str
    label: str
    kind: str
    build: Callable[[str, str, str, str | None, str | None], str]
    verified: bool
    evidence: str


def _google_flights(origin: str, destination: str, outbound: str,
                    ret: str | None, cabin: str | None) -> str:
    phrase = f"Flights from {origin} to {destination} on {outbound}"
    if ret:
        phrase += f" returning {ret}"
    if cabin:
        phrase += f" {cabin.replace('_', ' ')} class"
    return "https://www.google.com/travel/flights?q=" + quote_plus(phrase)


#: Kayak spells cabins differently from our canonical vocabulary.
_KAYAK_CABIN = {
    "economy": "economy",
    "premium_economy": "premium",
    "business": "business",
    "first": "first",
}


def _kayak(origin: str, destination: str, outbound: str,
           ret: str | None, cabin: str | None) -> str:
    path = f"{origin}-{destination}/{outbound}"
    if ret:
        path += f"/{ret}"
    url = f"https://www.kayak.com/flights/{path}"
    segment = _KAYAK_CABIN.get(cabin or "")
    # Economy is Kayak's default; appending it is noise.
    if segment and segment != "economy":
        url += f"/{segment}"
    return url


CHANNELS: tuple[Channel, ...] = (
    Channel(
        key="google_flights",
        label="Google Flights",
        kind="meta",
        build=_google_flights,
        verified=True,
        evidence=(
            "2026-08-16: opened BOG->NRT 2026-10-01/2026-10-21 business. Page titled "
            "the correct city pair, 11 results, 'departing 2026-10-01 and returning "
            "2026-10-21', top results labelled 'Business Class + First Class'."
        ),
    ),
    Channel(
        key="kayak",
        label="Kayak",
        kind="meta",
        build=_kayak,
        verified=True,
        evidence=(
            "2026-08-16: opened BOG->NRT 2026-10-01/2026-10-21 business. Page titled "
            "'BOG to NRT, 10/1 - 10/21', real priced results, cabin filter applied "
            "('Results include flights with mixed cabin classes')."
        ),
    ),
)


def booking_links(origin: str, destination: str, dates: str,
                  cabin: str | None = None,
                  channels: tuple[Channel, ...] | None = None) -> dict[str, Any]:
    """Search URLs for a trip, one per verified channel.

    Carries no price, by construction. A price belongs to a fetched fare object
    and must travel with one; this layer only ever answers "where do I click".
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed = V.normalize_dates(dates)
    cabin_v = V.validate_cabin(cabin)
    registry = CHANNELS if channels is None else channels

    links: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    for channel in registry:
        if not channel.verified:
            withheld.append({
                "channel": channel.key,
                "label": channel.label,
                "reason": "URL format not verified against the live site; "
                          "emitting it could send you to a dead search.",
            })
            continue
        links.append({
            "channel": channel.key,
            "label": channel.label,
            "kind": channel.kind,
            "url": channel.build(o, d, parsed["outbound"], parsed["return"], cabin_v),
        })

    return {
        "origin": o,
        "destination": d,
        "dates": parsed,
        "cabin": cabin_v,
        "links": links,
        "withheld": withheld,
    }
