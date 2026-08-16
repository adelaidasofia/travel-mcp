"""Behavior tests for the booking link layer.

The link layer turns Geo from an advisor into an agent: every fare tool can say
"this is a good fare on this route", and this module is what says "and here is
where you click".

Two safety properties are load-bearing and are tested as behavior, not shape:

  1. A URL is CONSTRUCTED BY CODE from validated inputs. The model never emits
     one. That is the structural enforcement of "the price and the link must
     come from the same fetched object" -- this layer holds no price at all, so
     a model-invented price can never arrive wearing a booking link.

  2. A channel whose URL format has not been verified against the live site is
     WITHHELD, not guessed. A link that lands on a 404 or a blank search is
     worse than no link, because the link itself signifies "this was checked".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_emits_a_link_carrying_the_route_and_both_dates(travel_vault):
    """Tracer bullet: a round trip produces a clickable link for that trip."""
    import booking

    result = booking.booking_links("BOG", "NRT", "2026-10-01..2026-10-21", cabin="business")

    urls = [link["url"] for link in result["links"]]
    assert urls, "expected at least one verified booking link"
    assert any(
        "BOG" in url and "NRT" in url and "2026-10-01" in url and "2026-10-21" in url
        for url in urls
    ), f"no link carried the full round trip: {urls}"


def test_builds_links_without_ever_reaching_the_model(travel_vault, monkeypatch):
    """A link can never carry a model-invented price, because no model runs here.

    This is the structural half of "the price and the link come from the same
    fetched object". The link layer is unreachable from the LLM: if the router
    were ever on this path, this test detonates.
    """
    import router

    def _detonate(*args, **kwargs):
        raise AssertionError("booking_links reached the LLM router")

    monkeypatch.setattr(router, "call_claude_text", _detonate)

    import booking

    result = booking.booking_links("BOG", "NRT", "2026-10-01..2026-10-21", cabin="first")

    assert result["links"], "expected links to be built without any model call"


def test_withholds_a_channel_whose_url_format_was_never_verified(travel_vault):
    """An unverified format is withheld with a reason, never guessed at.

    A dead link is worse than no link: the link itself signifies "this was
    checked". Withholding keeps that signal honest.
    """
    import booking

    registry = (
        booking.Channel(
            key="proven", label="Proven", kind="meta",
            build=lambda o, d, ob, r, c: f"https://proven.test/{o}-{d}",
            verified=True, evidence="opened live, correct route and dates",
        ),
        booking.Channel(
            key="guessed", label="Guessed", kind="ota",
            build=lambda o, d, ob, r, c: f"https://guessed.test/{o}-{d}",
            verified=False, evidence="",
        ),
    )

    result = booking.booking_links("BOG", "NRT", "2026-10-01", channels=registry)

    assert [link["channel"] for link in result["links"]] == ["proven"]
    assert [held["channel"] for held in result["withheld"]] == ["guessed"]
    assert result["withheld"][0]["reason"], "a withheld channel must say why"


def test_every_shipped_channel_that_emits_links_carries_its_verification_evidence(travel_vault):
    """Invariant, not a snapshot: whatever ships verified must show its work.

    Deliberately does not assert WHICH channels exist or how many -- that would
    break every time a channel is added, without covering any behavior.
    """
    import booking

    emitting = [channel for channel in booking.CHANNELS if channel.verified]
    assert emitting, "the shipped registry emits no links at all"
    for channel in emitting:
        assert channel.evidence.strip(), f"{channel.key} claims verified with no evidence"


def test_a_one_way_link_never_carries_a_return_date(travel_vault):
    """Booking a return she did not ask for is a wrong trip, silently."""
    import booking

    result = booking.booking_links("BOG", "MAD", "2026-11-05, one-way")

    assert result["dates"]["return"] is None
    for link in result["links"]:
        assert link["url"].count("2026-") == 1, (
            f"one-way link carries a second date: {link['url']}"
        )


@pytest.mark.parametrize("override", [
    {"origin": "Frankfurt"},
    {"destination": "Tokyo"},
    {"dates": "sometime next spring"},
    {"cabin": "luxury"},
])
def test_refuses_unvalidated_input_rather_than_emitting_a_link(travel_vault, override):
    """Nothing reaches a URL without passing the rails first.

    A link built from unvalidated input is how a typo becomes a booking for the
    wrong city on the wrong day.
    """
    import booking
    import validators

    call = {"origin": "BOG", "destination": "NRT", "dates": "2026-10-01", **override}

    with pytest.raises(validators.ValidationError):
        booking.booking_links(**call)


def test_the_link_layer_is_reachable_as_an_mcp_tool(travel_vault):
    """Geo can only hand over a link if the tool is actually exposed."""
    import server

    tool = getattr(server, "booking_links", None)
    assert tool is not None, "booking_links is not exposed as an MCP tool"

    fn = getattr(tool, "fn", tool)
    result = fn("BOG", "NRT", "2026-10-01..2026-10-21", cabin="business")

    assert result["links"], "the tool returned no links"
    assert all(link["url"].startswith("https://") for link in result["links"])
