"""travel-mcp v0.1.0 — Analytical travel-planner MCP.

21 tools across four surfaces:

  PROFILE (6)            — healthcheck, get/update travel profile, companion CRUD
  PERSISTENCE (3)        — save/list/get trip plan
  ANALYZERS (7)          — analyze_route, pricing_reality_check, geo_pricing_arbitrage,
                            timing_sweet_spot, fare_rules_analysis, channel_comparison,
                            tracking_strategy
  PDF-DERIVED (5)        — trip_prep_brief, emergency_travel_card, compare_trips,
                            post_trip_review, price_drop_analysis

Safety model:
  - NO draft+confirm (this MCP is analysis, not destructive prod mutation).
  - NO daily USD cap (Tier-1 Claude Max = subscription, not per-token).
  - 4-field observability JSONL audit on every call.
  - sanitize_error() on every error/output before the seam.
  - INPUT rail validation (validators.py) before any LLM call.
  - Prompt-caching marker on every system block (router.py).

Auth cascade: Claude Max CLI → Anthropic API key (router.py).

This MCP does NOT book travel — that requires real-time flight data + payment.
For real booking, compose with Claude in Chrome (per the MaverickAI Travel Agent
Guide pattern) or a sibling API-wrapping MCP (salamentic/google-flights-mcp,
ravinahp/flights-mcp, etc.).
"""

from __future__ import annotations

import os
import profile as profile_mod
from typing import Any

from fastmcp import FastMCP

import audit
import prompts
import router
import validators as V

mcp = FastMCP("travel-mcp")


def _profile_or_none() -> dict[str, Any] | None:
    """Read profile if vault env is set, else return None silently."""
    try:
        return profile_mod.read_profile()
    except Exception:
        return None


def _llm_call(tool: str, input_payload: dict[str, Any],
              system: str, user: str, model: str | None = None) -> dict[str, Any]:
    """Run an LLM-backed tool with audit + sanitize wrapping."""
    with audit.timed(tool, input_payload=input_payload) as ctx:
        result = router.call_claude_text(system=system, user=user, model=model)
        ctx["token_usage"] = result.token_usage
        ctx["extra"] = {
            "auth": result.auth,
            "model": result.model,
            "router_elapsed_ms": result.elapsed_ms,
        }
        output = {
            "text": result.text,
            "auth": result.auth,
            "model": result.model,
            "token_usage": result.token_usage,
            "elapsed_ms": result.elapsed_ms,
        }
        ctx["output"] = {"text_chars": len(result.text), "auth": result.auth}
        return output


# ============================ HEALTH + PROFILE ============================

@mcp.tool()
def healthcheck() -> dict[str, Any]:
    """Verify the MCP is configured + ready: vault path resolved, profile present, router available.

    Auto-creates the 🧳 Travel/ folder tree + seeds Profile.md on first call when
    TRAVEL_MCP_VAULT_PATH is set. Idempotent — re-running once everything exists is a no-op.

    Returns:
      ok: bool                       — overall ready state
      vault_path: str | None         — resolved vault root
      travel_folder: str             — folder name within vault (default "🧳 Travel")
      profile_exists: bool           — Profile.md present
      companions_count: int          — files in Companions/
      trips_count: int               — files in Trips/
      router: dict                   — Claude routing availability
      created: list[str]             — paths created this call
      error_class: str | None        — populated when ok=false
    """
    with audit.timed("healthcheck", input_payload={}) as ctx:
        vault = os.environ.get(profile_mod.VAULT_ENV)
        if not vault:
            out = {
                "ok": False,
                "vault_path": None,
                "travel_folder": profile_mod._folder_name(),
                "profile_exists": False,
                "companions_count": 0,
                "trips_count": 0,
                "router": router.router_status(),
                "created": [],
                "error_class": "missing_env",
                "hint": (
                    f"Set {profile_mod.VAULT_ENV} in admin.env or your .mcp.json env block "
                    "to your vault root (absolute path)."
                ),
            }
            ctx["output"] = out
            ctx["error_class"] = "missing_env"
            return out
        try:
            status = profile_mod.ensure_dirs()
        except Exception as exc:
            ctx["error_class"] = audit.classify_error(exc)
            out = {
                "ok": False,
                "vault_path": vault,
                "travel_folder": profile_mod._folder_name(),
                "profile_exists": False,
                "companions_count": 0,
                "trips_count": 0,
                "router": router.router_status(),
                "created": [],
                "error_class": ctx["error_class"],
                "hint": audit.sanitize_error(str(exc)),
            }
            ctx["output"] = out
            return out
        td = profile_mod.trips_dir()
        cd = profile_mod.companions_dir()
        pp = profile_mod.profile_path()
        out = {
            "ok": True,
            "vault_path": vault,
            "travel_folder": profile_mod._folder_name(),
            "profile_exists": bool(pp and pp.exists()),
            "companions_count": (len(list(cd.glob("*.md"))) if cd and cd.exists() else 0),
            "trips_count": (len(list(td.glob("*.md"))) if td and td.exists() else 0),
            "router": router.router_status(),
            "created": status["created"],
            "error_class": None,
        }
        ctx["output"] = out
        return out


@mcp.tool()
def get_travel_profile() -> dict[str, Any]:
    """Return the master traveler profile (frontmatter + body) from 🧳 Travel/Profile.md.

    Profile is the source of truth for: name, DOB, passport, KTN, loyalty IDs,
    credit cards, cabin rules, schedule rules, hotel chains, hard booking rules.
    Edit via Obsidian directly OR via update_travel_profile_section().
    """
    with audit.timed("get_travel_profile", input_payload={}) as ctx:
        result = profile_mod.read_profile()
        ctx["output"] = {"path": result["path"], "body_chars": len(result["body"]),
                         "frontmatter_keys": sorted(result["frontmatter"].keys())}
        return result


@mcp.tool()
def update_travel_profile_section(section_heading: str, new_content: str) -> dict[str, Any]:
    """Replace one `## Section` body in Profile.md.

    section_heading is matched verbatim (e.g. "4. Airports & Flights"). If the
    section doesn't exist it is appended. Adjacent sections are preserved.
    """
    with audit.timed("update_travel_profile_section",
                     input_payload={"section_heading": section_heading,
                                    "new_content": V.truncate(new_content)}) as ctx:
        result = profile_mod.update_profile_section(section_heading, new_content)
        ctx["output"] = result
        return result


@mcp.tool()
def list_companion_profiles() -> dict[str, Any]:
    """List all companion traveler profiles under 🧳 Travel/Companions/."""
    with audit.timed("list_companion_profiles", input_payload={}) as ctx:
        items = profile_mod.list_companions()
        out = {"count": len(items), "companions": items}
        ctx["output"] = {"count": len(items)}
        return out


@mcp.tool()
def get_companion_profile(name: str) -> dict[str, Any]:
    """Return one companion profile by name."""
    with audit.timed("get_companion_profile", input_payload={"name": name}) as ctx:
        result = profile_mod.read_companion(name)
        ctx["output"] = {"path": result["path"], "name": result["name"]}
        return result


@mcp.tool()
def upsert_companion_profile(
    name: str,
    legal_name: str | None = None,
    date_of_birth: str | None = None,
    passport: str | None = None,
    passport_expiry: str | None = None,
    ktn: str | None = None,
    seat_preference: str | None = None,
    airline_loyalty: str | None = None,
    hotel_loyalty: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """Create or update a companion traveler profile.

    Use for partner, family member, frequent travel buddy. All fields optional —
    pass only what you want to set/change. `body` overwrites the markdown body
    (use sparingly; the frontmatter holds structured fields).
    """
    fields = {
        "legal_name": legal_name,
        "date_of_birth": date_of_birth,
        "passport": passport,
        "passport_expiry": passport_expiry,
        "ktn": ktn,
        "seat_preference": seat_preference,
        "airline_loyalty": airline_loyalty,
        "hotel_loyalty": hotel_loyalty,
    }
    with audit.timed("upsert_companion_profile",
                     input_payload={"name": name, "fields": fields}) as ctx:
        result = profile_mod.upsert_companion(name, fields, body=body)
        ctx["output"] = result
        return result


# ============================ TRIP-PLAN PERSISTENCE ============================

@mcp.tool()
def save_trip_plan(
    slug: str,
    summary: str,
    content: str,
    destination: str | None = None,
    outbound_date: str | None = None,
    return_date: str | None = None,
    total_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Persist a trip plan to 🧳 Travel/Trips/<slug>.md.

    Use to save the output of an analyzer (analyze_route, compare_trips, etc.)
    for future reference. The frontmatter carries destination + dates + cost so
    list_trip_plans() can filter/sort.
    """
    safe_slug = V.validate_slug(slug)
    fm_extra: dict[str, Any] = {
        "destination": destination,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "total_cost_usd": total_cost_usd,
    }
    with audit.timed("save_trip_plan",
                     input_payload={"slug": safe_slug, "summary": V.truncate(summary),
                                    "content_chars": len(content), "frontmatter": fm_extra}) as ctx:
        result = profile_mod.save_trip(safe_slug, summary, content, fm_extra)
        ctx["output"] = result
        return result


@mcp.tool()
def list_trip_plans(year: int | None = None, destination_contains: str | None = None) -> dict[str, Any]:
    """List saved trip plans. Optional year filter + destination substring."""
    with audit.timed("list_trip_plans",
                     input_payload={"year": year, "destination_contains": destination_contains}) as ctx:
        items = profile_mod.list_trips(year=year, destination_contains=destination_contains)
        out = {"count": len(items), "trips": items}
        ctx["output"] = {"count": len(items)}
        return out


@mcp.tool()
def get_trip_plan(slug: str) -> dict[str, Any]:
    """Read one saved trip plan by slug."""
    safe_slug = V.validate_slug(slug)
    with audit.timed("get_trip_plan", input_payload={"slug": safe_slug}) as ctx:
        result = profile_mod.read_trip(safe_slug)
        ctx["output"] = {"path": result["path"], "body_chars": len(result["body"])}
        return result


# ================================ ANALYZERS ================================

@mcp.tool()
def analyze_route(
    origin: str,
    destination: str,
    dates: str,
    passengers: int = 1,
    cabin: str | None = None,
    flexibility: str | None = None,
    risk_tolerance: str = "medium",
) -> dict[str, Any]:
    """Break a route into cheaper-alternative categories and rank by cost / time / risk.

    Categories: direct baseline, hidden-city/skiplagging (with legitimacy flags),
    nearby-airport substitutions, multi-leg/self-transfer, open-jaw/stopover.

    risk_tolerance ∈ {low, medium, high} — shapes the final top-3 shortlist.
    dates accepts "YYYY-MM-DD" (one-way) or "YYYY-MM-DD..YYYY-MM-DD" (round-trip).
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed_dates = V.normalize_dates(dates)
    cabin_v = V.validate_cabin(cabin)
    risk_v = V.validate_risk_tolerance(risk_tolerance)
    if passengers < 1 or passengers > 9:
        raise V.ValidationError("passengers must be 1-9")
    profile = _profile_or_none()
    payload = {"origin": o, "destination": d, "dates": parsed_dates, "passengers": passengers,
               "cabin": cabin_v, "flexibility": flexibility, "risk_tolerance": risk_v}
    return _llm_call(
        "analyze_route", payload,
        system=prompts.ANALYZE_ROUTE_SYSTEM,
        user=prompts.render_analyze_route_user(o, d, parsed_dates, passengers, cabin_v,
                                                flexibility, risk_v, profile),
    )


@mcp.tool()
def pricing_reality_check(route_context: str | None = None) -> dict[str, Any]:
    """Three-bucket taxonomy of how airline + OTA pricing actually works.

    Confirmed mechanisms (revenue management, fare buckets, POS, currency) vs
    plausible-but-unconfirmed (cookies, IP, device fingerprinting) vs myths.
    Ends with a search method using only bucket-A mechanisms.

    Optional route_context narrows the analysis (e.g. "BOG-JFK round trip").
    """
    return _llm_call(
        "pricing_reality_check", {"route_context": route_context},
        system=prompts.PRICING_REALITY_CHECK_SYSTEM,
        user=prompts.render_pricing_reality_check_user(route_context),
    )


@mcp.tool()
def geo_pricing_arbitrage(
    origin: str, destination: str, dates: str, cabin: str | None = None,
) -> dict[str, Any]:
    """Compare same-itinerary pricing across home-country / origin / destination / third-country POS.

    Lists the legal ways to actually book a foreign POS fare with per-method
    catches (multi-currency cards, OTAs in cheaper markets, VPN+T&Cs risk,
    award tickets via foreign FFP). Refuses anything involving misrepresentation.
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed = V.normalize_dates(dates)
    cabin_v = V.validate_cabin(cabin)
    payload = {"origin": o, "destination": d, "dates": parsed, "cabin": cabin_v}
    return _llm_call(
        "geo_pricing_arbitrage", payload,
        system=prompts.GEO_PRICING_SYSTEM,
        user=prompts.render_geo_pricing_user(o, d, parsed, cabin_v),
    )


@mcp.tool()
def timing_sweet_spot(
    origin: str, destination: str, trip_type: str = "leisure", target_month: str | None = None,
) -> dict[str, Any]:
    """When to book + when to fly for the cheapest total cost on a route.

    Uses TYPICAL HISTORICAL pricing patterns (not live data). Returns booking
    window (short-haul vs regional vs long-haul), cheapest day-of-week, cheapest
    weeks of the year, fare-reset patterns (evidence-backed vs anecdotal),
    spike events, and a concrete action plan with dates.
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    tt = V.validate_trip_type(trip_type)
    payload = {"origin": o, "destination": d, "trip_type": tt, "target_month": target_month}
    return _llm_call(
        "timing_sweet_spot", payload,
        system=prompts.TIMING_SWEET_SPOT_SYSTEM,
        user=prompts.render_timing_sweet_spot_user(o, d, tt, target_month),
    )


@mcp.tool()
def fare_rules_analysis(
    origin: str, destination: str, dates: str, carriers: list[str] | None = None,
) -> dict[str, Any]:
    """Plain-English fare rules + legitimate-savings examples per category.

    Covers: fare basis codes, advance/min-stay/Saturday-night rules, round-trip
    vs two one-ways, open-jaw/stopover/married-segment logic, refundability,
    codeshare vs operating carrier, promo + corporate fare codes.

    Classifies every tactic into LEGITIMATE / AGAINST-T&Cs / OFF-LIMITS tiers.
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed = V.normalize_dates(dates)
    payload = {"origin": o, "destination": d, "dates": parsed, "carriers": carriers}
    return _llm_call(
        "fare_rules_analysis", payload,
        system=prompts.FARE_RULES_SYSTEM,
        user=prompts.render_fare_rules_user(o, d, parsed, carriers),
    )


@mcp.tool()
def channel_comparison(
    origin: str, destination: str, dates: str, cabin: str | None = None,
) -> dict[str, Any]:
    """Where to actually book: airline direct vs major OTA vs aggressive-pricing OTA vs agent.

    For each channel: typical price position, hidden fees, what you give up
    (voucher refunds, weak irrops handling), documented misleading practices.
    Ends with a decision rule for THIS route.
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed = V.normalize_dates(dates)
    cabin_v = V.validate_cabin(cabin)
    payload = {"origin": o, "destination": d, "dates": parsed, "cabin": cabin_v}
    return _llm_call(
        "channel_comparison", payload,
        system=prompts.CHANNEL_COMPARISON_SYSTEM,
        user=prompts.render_channel_comparison_user(o, d, parsed, cabin_v),
    )


@mcp.tool()
def tracking_strategy(
    origin: str, destination: str, dates: str, cabin: str | None = None,
    budget_ceiling: float | None = None, weeks_out: int | None = None,
) -> dict[str, Any]:
    """Design a fare-tracking strategy for the weeks before departure.

    Tools (Google Flights tracking, Hopper, Kayak, Going, native airline),
    search hygiene (evidence vs folklore on cookies/IP), cadence without
    burnout, concrete decision rules ("if price drops X% below 30d median,
    book"), backstops (24h-hold, fare-lock, refundable as insurance), and a
    worked one-month example.
    """
    o = V.validate_iata(origin, field="origin")
    d = V.validate_iata(destination, field="destination")
    parsed = V.normalize_dates(dates)
    cabin_v = V.validate_cabin(cabin)
    payload = {"origin": o, "destination": d, "dates": parsed, "cabin": cabin_v,
               "budget_ceiling": budget_ceiling, "weeks_out": weeks_out}
    return _llm_call(
        "tracking_strategy", payload,
        system=prompts.TRACKING_STRATEGY_SYSTEM,
        user=prompts.render_tracking_strategy_user(o, d, parsed, cabin_v, budget_ceiling, weeks_out),
    )


# ============================== PDF-DERIVED ==============================

@mcp.tool()
def trip_prep_brief(
    destination: str, dates: str, hotel: str | None = None,
    purpose: str = "leisure", traveling_with: str | None = None, vibe: str | None = None,
) -> dict[str, Any]:
    """One-page pre-departure brief: weather + packing, restaurants near hotel, meetings,
    one local thing for a free hour, getting around, what you might forget, money, one
    personal note. Under 500 words. Real names, real links, real times.
    """
    parsed = V.normalize_dates(dates)
    profile = _profile_or_none()
    payload = {"destination": destination, "dates": parsed, "hotel": hotel,
               "purpose": purpose, "traveling_with": traveling_with, "vibe": vibe}
    return _llm_call(
        "trip_prep_brief", payload,
        system=prompts.TRIP_PREP_BRIEF_SYSTEM,
        user=prompts.render_trip_prep_brief_user(destination, parsed, hotel, purpose,
                                                  traveling_with, vibe, profile),
    )


@mcp.tool()
def emergency_travel_card(
    destination: str, dates: str, hotel: str | None = None,
) -> dict[str, Any]:
    """Single-page emergency reference: numbers, contingency plans, practical info.

    Emergency numbers + embassy + hospital + travel insurance + airline rebooking.
    Contingency plans for flight cancellation, hotel falling through, lost passport,
    stolen card. Practical info: hotel address in local language, local ride-share
    app, whether phone plan works. Save as PDF or screenshot to phone.
    """
    parsed = V.normalize_dates(dates)
    profile = _profile_or_none()
    payload = {"destination": destination, "dates": parsed, "hotel": hotel}
    return _llm_call(
        "emergency_travel_card", payload,
        system=prompts.EMERGENCY_TRAVEL_CARD_SYSTEM,
        user=prompts.render_emergency_travel_card_user(destination, parsed, hotel, profile),
    )


@mcp.tool()
def compare_trips(
    option_a_destination: str, option_a_outbound: str, option_a_return: str | None,
    option_b_destination: str, option_b_outbound: str, option_b_return: str | None,
    option_a_purpose: str = "leisure", option_b_purpose: str = "leisure",
) -> dict[str, Any]:
    """Side-by-side trip comparison: flight, hotel, total cost, weather, points
    strategy, visa, the "better" line, the "watch out" line. Ends with a
    recommendation + tiebreaker if close.
    """
    a_out = V.validate_iso_date(option_a_outbound, field="option_a_outbound")
    b_out = V.validate_iso_date(option_b_outbound, field="option_b_outbound")
    a_ret = V.validate_iso_date(option_a_return, field="option_a_return") if option_a_return else None
    b_ret = V.validate_iso_date(option_b_return, field="option_b_return") if option_b_return else None
    a = {"destination": option_a_destination, "outbound": a_out, "return": a_ret,
         "purpose": V.validate_trip_type(option_a_purpose)}
    b = {"destination": option_b_destination, "outbound": b_out, "return": b_ret,
         "purpose": V.validate_trip_type(option_b_purpose)}
    profile = _profile_or_none()
    return _llm_call(
        "compare_trips", {"option_a": a, "option_b": b},
        system=prompts.COMPARE_TRIPS_SYSTEM,
        user=prompts.render_compare_trips_user(a, b, profile),
    )


@mcp.tool()
def post_trip_review(
    destination: str, dates: str, booking_data: str | None = None,
) -> dict[str, Any]:
    """Post-trip spending review: total cost breakdown, points + miles earned,
    what went well, what to do differently, profile-update suggestions.

    booking_data: optional paste of booking confirmations / Amex statements /
    loyalty-account screenshots. If absent, the review estimates from typical
    ranges and flags every number to verify.
    """
    parsed = V.normalize_dates(dates)
    profile = _profile_or_none()
    payload = {"destination": destination, "dates": parsed,
               "booking_data_chars": len(booking_data or "")}
    return _llm_call(
        "post_trip_review", payload,
        system=prompts.POST_TRIP_REVIEW_SYSTEM,
        user=prompts.render_post_trip_review_user(destination, parsed, booking_data, profile),
    )


@mcp.tool()
def price_drop_analysis(
    carrier: str, flight_number: str, departure_date: str,
    paid_price: float, cabin: str | None = None,
) -> dict[str, Any]:
    """Post-booking fare-drop monitor. Estimates current price vs paid price,
    surfaces carrier rebooking policy, and gives exact steps to claim credit /
    rebook at the lower fare (or "no change" if the price held).

    Run daily via scheduled task until 24h before departure.
    """
    dep = V.validate_iso_date(departure_date, field="departure_date")
    cabin_v = V.validate_cabin(cabin)
    if paid_price <= 0 or paid_price > 100000:
        raise V.ValidationError("paid_price must be > 0 and < 100000")
    payload = {"carrier": carrier, "flight_number": flight_number,
               "departure_date": dep, "paid_price": paid_price, "cabin": cabin_v}
    return _llm_call(
        "price_drop_analysis", payload,
        system=prompts.PRICE_DROP_ANALYSIS_SYSTEM,
        user=prompts.render_price_drop_analysis_user(carrier, flight_number, dep, cabin_v, paid_price),
    )


if __name__ == "__main__":
    mcp.run()
