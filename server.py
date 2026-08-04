"""travel-mcp — Analytical travel-planner MCP.

42 tools across six surfaces:

  PROFILE (6)            — healthcheck, get/update travel profile, companion CRUD
  PERSISTENCE (3)        — save/list/get trip plan
  ANALYZERS (7)          — analyze_route, pricing_reality_check, geo_pricing_arbitrage,
                            timing_sweet_spot, fare_rules_analysis, channel_comparison,
                            tracking_strategy
  PDF-DERIVED (5)        — trip_prep_brief, emergency_travel_card, compare_trips,
                            post_trip_review, price_drop_analysis
  ITINERARY MODEL (21)   — structured trips (ordered segments), preference profile v2,
                            been-there ledger with provenance, dwell-time ranges,
                            entry-eligibility gate, backward-scheduled prerequisites

The itinerary surface is DATA, not prose: it does not call an LLM, so those
tools are deterministic, offline and free. The analyzers remain LLM-backed.

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
import dwell as dwell_mod
import eligibility as eligibility_mod
import itinerary as itinerary_mod
import ledger as ledger_mod
import preferences as preferences_mod
import prereqs as prereqs_mod
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
    passport_country: str | None = None,
    passport_expiry: str | None = None,
    second_passport_country: str | None = None,
    second_passport_expiry: str | None = None,
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

    NEVER pass a passport, national ID or SSN number — this tool does not accept
    one and the vault write boundary refuses it. Border eligibility is decided by
    ISSUING COUNTRY and EXPIRY DATE; the document number is read by nothing here.
    Dual nationals: fill the `second_passport_*` pair so per-border checks can
    compare both and report which one they used.
    """
    fields = {
        "legal_name": legal_name,
        "date_of_birth": date_of_birth,
        "passport_country": passport_country,
        "passport_expiry": passport_expiry,
        "second_passport_country": second_passport_country,
        "second_passport_expiry": second_passport_expiry,
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


# ============================== ITINERARY MODEL ==============================
# Deterministic, offline, no LLM call. A trip is ordered segments; everything
# below is downstream of that one primitive.

@mcp.tool()
def save_itinerary(
    slug: str,
    title: str,
    window_start: str,
    window_end: str,
    segments: list[dict[str, Any]] | None = None,
    notes: str | None = None,
    scope: str = "personal",
) -> dict[str, Any]:
    """Create or replace a structured itinerary at 🧳 Travel/Itineraries/<slug>.md.

    A trip is ORDERED SEGMENTS inside a departure..return window. Each segment is
    {city, country, arrive, depart, status, scope}:

      status  locked | planned | candidate   (candidate = a proposal to fill a window)
      scope   personal | company

    Segments are sorted by arrival on write, so pass them in any order. Returns the
    stored trip plus its open windows and any overlap conflicts.
    """
    trip = itinerary_mod.Trip.build(
        slug=slug, title=title, window_start=window_start, window_end=window_end,
        segments=segments or [], notes=notes, scope=scope,
    )
    with audit.timed("save_itinerary",
                     input_payload={"slug": trip.slug, "window": [trip.window_start, trip.window_end],
                                    "segments": len(trip.segments)}) as ctx:
        # Candidate segments are PROPOSALS, so they clear the eligibility gate
        # before they can land. Locked and planned segments are facts and pass
        # through untouched.
        eligibility_mod.guard_proposed_countries(trip.proposed_countries())
        written = itinerary_mod.save(trip)
        out = {
            **written,
            "trip": trip.to_dict(),
            "open_windows": [w.to_dict() for w in trip.open_windows()],
            "conflicts": trip.conflicts(),
            "out_of_window": trip.out_of_window(),
        }
        ctx["output"] = {"path": written["path"], "segments": len(trip.segments)}
        return out


@mcp.tool()
def get_itinerary(slug: str) -> dict[str, Any]:
    """Read one structured itinerary, with its open windows and conflicts."""
    with audit.timed("get_itinerary", input_payload={"slug": slug}) as ctx:
        trip = itinerary_mod.load(slug)
        out = {
            "trip": trip.to_dict(),
            "open_windows": [w.to_dict() for w in trip.open_windows()],
            "conflicts": trip.conflicts(),
            "out_of_window": trip.out_of_window(),
        }
        ctx["output"] = {"slug": trip.slug, "segments": len(trip.segments)}
        return out


@mcp.tool()
def list_itineraries() -> dict[str, Any]:
    """List every structured itinerary with its segment + open-window counts."""
    with audit.timed("list_itineraries", input_payload={}) as ctx:
        items = itinerary_mod.list_all()
        ctx["output"] = {"count": len(items)}
        return {"count": len(items), "itineraries": items}


@mcp.tool()
def add_itinerary_segment(
    slug: str,
    city: str,
    country: str,
    arrive: str,
    depart: str,
    status: str = "planned",
    scope: str = "personal",
    note: str | None = None,
) -> dict[str, Any]:
    """Append one segment to an existing itinerary and re-sort by arrival date.

    Returns the recomputed open windows so the caller sees immediately what the
    new segment consumed, plus any overlap conflicts and any segment that fell
    outside the trip window. Overlaps are reported, never silently merged.

    A `candidate` segment is a PROPOSAL, so its country must be verified-eligible
    first; `locked` and `planned` record a decision already made and are stored
    as given.
    """
    segment = itinerary_mod.Segment.build(
        city=city, country=country, arrive=arrive, depart=depart,
        status=status, scope=scope, note=note,
    )
    with audit.timed("add_itinerary_segment",
                     input_payload={"slug": slug, "segment": segment.to_dict()}) as ctx:
        if segment.status == itinerary_mod.PROPOSAL_STATUS:
            eligibility_mod.guard_proposed_countries([segment.country])
        trip = itinerary_mod.load(slug).with_segment(segment)
        written = itinerary_mod.save(trip)
        out = {
            **written,
            "trip": trip.to_dict(),
            "open_windows": [w.to_dict() for w in trip.open_windows()],
            "conflicts": trip.conflicts(),
            # Same shape as save_itinerary / get_itinerary. Omitting it let a
            # segment land wholly outside the window and still look clean.
            "out_of_window": trip.out_of_window(),
        }
        ctx["output"] = {"slug": trip.slug, "segments": len(trip.segments)}
        return out


@mcp.tool()
def itinerary_open_windows(slug: str, include_candidates: bool = False) -> dict[str, Any]:
    """Unallocated stretches inside the trip window.

    A segment occupies both its arrive day and its depart day, so a window runs
    from (previous depart + 1) to (next arrive - 1). At the edges of the trip
    window there is no boundary day to give up.

    By default only `locked` and `planned` segments consume time — a `candidate`
    is a proposal to FILL a window, so it must not eat one. Pass
    include_candidates=true to see what the window looks like if every candidate
    were accepted; that answer PROPOSES those countries, so it is refused unless
    every one of them is verified-eligible.
    """
    statuses = ("locked", "planned", "candidate") if include_candidates else itinerary_mod.COMMITTED_STATUSES
    with audit.timed("itinerary_open_windows",
                     input_payload={"slug": slug, "include_candidates": include_candidates}) as ctx:
        trip = itinerary_mod.load(slug)
        if include_candidates:
            eligibility_mod.guard_proposed_countries(trip.proposed_countries())
        windows = trip.open_windows(statuses)
        out = {
            "slug": trip.slug,
            "window_start": trip.window_start,
            "window_end": trip.window_end,
            "window_days": trip.window_days,
            "counted_statuses": list(statuses),
            "open_windows": [w.to_dict() for w in windows],
            "open_days_total": sum(w.days for w in windows),
        }
        ctx["output"] = {"slug": trip.slug, "windows": len(windows)}
        return out


@mcp.tool()
def plan_window_packing(
    slug: str,
    hours_of_interest: float | None = None,
    completionism: float = 0.6,
    discretionary_hours: float | None = None,
    hours_low: float | None = None,
    hours_high: float | None = None,
    overhead_days: int = 1,
    include_candidates: bool = False,
) -> dict[str, Any]:
    """How many places fit in each open window, as a range.

    Combines the dwell-time range with k_max = floor((L + overhead) / (dwell + overhead)).
    Longer stays mean fewer places, so the low end of the dwell range produces the
    HIGH end of the place count.

    discretionary_hours defaults to the saved preference profile's daily figure.
    overhead_days is the travel/transition cost between two places.

    include_candidates=true packs the windows as if every candidate were
    accepted. That output PROPOSES those countries, so it is refused unless every
    one of them is verified-eligible.
    """
    hours_per_day = discretionary_hours
    if hours_per_day is None:
        hours_per_day = preferences_mod.load().time_budget.discretionary_hours_per_day
    with audit.timed("plan_window_packing",
                     input_payload={"slug": slug, "completionism": completionism,
                                    "discretionary_hours": hours_per_day,
                                    "overhead_days": overhead_days}) as ctx:
        trip = itinerary_mod.load(slug)
        statuses = ("locked", "planned", "candidate") if include_candidates else itinerary_mod.COMMITTED_STATUSES
        if include_candidates:
            eligibility_mod.guard_proposed_countries(trip.proposed_countries())
        estimate = dwell_mod.estimate(
            trip.title, completionism=completionism, discretionary_hours=hours_per_day,
            hours_of_interest=hours_of_interest, hours_low=hours_low, hours_high=hours_high,
        )
        packed = []
        for w in trip.open_windows(statuses):
            packed.append({**w.to_dict(), **dwell_mod.pack_window(w.days, estimate, overhead_days)})
        out = {
            "slug": trip.slug,
            "dwell": estimate.to_dict(),
            "windows": packed,
        }
        ctx["output"] = {"slug": trip.slug, "windows": len(packed)}
        return out


@mcp.tool()
def dwell_time_estimate(
    place: str,
    completionism: float,
    discretionary_hours: float,
    hours_of_interest: float | None = None,
    hours_low: float | None = None,
    hours_high: float | None = None,
    hours_uncertainty: float = 0.25,
) -> dict[str, Any]:
    """Days to spend in one place: days = ceil(H * completionism / discretionary_hours), floor 2.

    ALWAYS a range with a confidence, never a point value — H is an estimate, so a
    single number would be false precision. Supply either hours_of_interest (the
    spread is derived and confidence caps at medium) or hours_low + hours_high (a
    measured range, which can reach high confidence).
    """
    with audit.timed("dwell_time_estimate",
                     input_payload={"place": place, "completionism": completionism,
                                    "discretionary_hours": discretionary_hours}) as ctx:
        estimate = dwell_mod.estimate(
            place, completionism=completionism, discretionary_hours=discretionary_hours,
            hours_of_interest=hours_of_interest, hours_low=hours_low, hours_high=hours_high,
            hours_uncertainty=hours_uncertainty,
        )
        out = estimate.to_dict()
        ctx["output"] = {"place": estimate.place, "days_low": estimate.days_low,
                         "days_high": estimate.days_high, "confidence": estimate.confidence}
        return out


# ---------------------------- PREFERENCE PROFILE v2 ----------------------------

@mcp.tool()
def get_preference_profile() -> dict[str, Any]:
    """Read preference profile v2: dials, time budget, places, standing seeds."""
    with audit.timed("get_preference_profile", input_payload={}) as ctx:
        prof = preferences_mod.load()
        out = prof.to_dict()
        ctx["output"] = {"places": len(prof.places), "seeds": len(prof.seeds)}
        return out


@mcp.tool()
def set_travel_dials(novelty: float, depth: float) -> dict[str, Any]:
    """Set the two INDEPENDENT dials (0.0-1.0 each).

    novelty = appetite for places you have not been. depth = appetite for going
    deep where you are. They are not opposites: wanting new places and wanting
    them properly is not a contradiction, so both may be 1.0.
    """
    with audit.timed("set_travel_dials",
                     input_payload={"novelty": novelty, "depth": depth}) as ctx:
        prof = preferences_mod.load().with_dials(novelty, depth)
        written = preferences_mod.save(prof)
        out = {**written, "dials": prof.dials.to_dict()}
        ctx["output"] = out
        return out


@mcp.tool()
def set_time_budget(
    discretionary_hours_per_day: float,
    work_start: str | None = None,
    work_end: str | None = None,
    work_days: list[str] | None = None,
) -> dict[str, Any]:
    """Set daily discretionary hours and the optional work-hours block.

    discretionary_hours_per_day is the free time on a NON-work day. On a work day
    the block is subtracted from it. Times are 24-hour HH:MM; work_days are
    mon..sun.
    """
    block = None
    if work_start or work_end:
        if not (work_start and work_end):
            raise V.ValidationError("supply BOTH work_start and work_end, or neither")
        block = {"start": work_start, "end": work_end, "days": work_days or []}
    with audit.timed("set_time_budget",
                     input_payload={"discretionary_hours_per_day": discretionary_hours_per_day,
                                    "work_block": block}) as ctx:
        prof = preferences_mod.load().with_time_budget(discretionary_hours_per_day, block)
        written = preferences_mod.save(prof)
        out = {**written, "time_budget": prof.time_budget.to_dict()}
        ctx["output"] = out
        return out


@mcp.tool()
def upsert_place_preference(
    place: str,
    relationships: list[str],
    country: str | None = None,
    seed_refs: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record what a place MEANS — as many relationships at once as apply.

    relationships (one or more): for-a-person | love-the-place | never-mind-it |
    been-done | want-to-go. A place can be for-a-person AND been-done AND
    love-the-place at the same time; forcing a single label loses the nuance that
    decides the trip.

    seed_refs point at standing seeds BY REFERENCE — the seed's values are never
    copied in, so editing a seed updates every place that references it.
    """
    pref = preferences_mod.PlacePreference.build(
        place=place, relationships=relationships, country=country,
        seed_refs=seed_refs or (), note=note,
    )
    with audit.timed("upsert_place_preference",
                     input_payload={"place": pref.place,
                                    "relationships": list(pref.relationships)}) as ctx:
        prof = preferences_mod.load().upsert_place(pref)
        written = preferences_mod.save(prof)
        out = {**written, "place": prof.resolve_place(pref.place)}
        ctx["output"] = {"place": pref.place, "relationships": list(pref.relationships)}
        return out


@mcp.tool()
def upsert_preference_seed(key: str, label: str | None = None,
                           values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create or update a standing seed that places point at by reference.

    Editing a seed changes every place referencing it, because places store the
    key and resolve at read time rather than copying the values.
    """
    seed = preferences_mod.Seed.build(key=key, label=label, values=values)
    with audit.timed("upsert_preference_seed", input_payload={"key": seed.key}) as ctx:
        prof = preferences_mod.load().upsert_seed(seed)
        written = preferences_mod.save(prof)
        referencing = [p.place for p in prof.places if seed.key in p.seed_refs]
        out = {**written, "seed": seed.to_dict(), "referencing_places": referencing}
        ctx["output"] = {"key": seed.key, "referencing_places": len(referencing)}
        return out


@mcp.tool()
def resolve_place_preference(place: str) -> dict[str, Any]:
    """Resolve a place's seeds AT READ TIME. Dangling refs are surfaced, not dropped."""
    with audit.timed("resolve_place_preference", input_payload={"place": place}) as ctx:
        resolved = preferences_mod.load().resolve_place(place)
        ctx["output"] = {"place": resolved["place"],
                         "dangling": resolved["dangling_seed_refs"]}
        return resolved


# ------------------------------ BEEN-THERE LEDGER ------------------------------

@mcp.tool()
def record_country_inference(
    country: str,
    source: str,
    claim: bool = True,
    note: str | None = None,
) -> dict[str, Any]:
    """Commit an UNCONFIRMED inference about a country visit.

    An inference the traveler has not confirmed counts as NOT VISITED and is
    committed as such right now — never left pending, never counted as a visit.
    The guess is preserved in `claim` so it can be asked about later, but no count
    reads it. Upgrade it with confirm_country_visit once the traveler answers.
    """
    with audit.timed("record_country_inference",
                     input_payload={"country": country, "claim": claim,
                                    "source": V.truncate(source, 200)}) as ctx:
        led = ledger_mod.load().record_inference(country, claim=claim, source=source, note=note)
        written = ledger_mod.save(led)
        record = led.get(country)
        out = {
            **written,
            "record": record.to_dict() if record else None,
            "count_visited": led.count_visited(),
            "rule": "unconfirmed inference is committed as NOT visited",
        }
        ctx["output"] = {"country": country, "visited": bool(record and record.visited)}
        return out


@mcp.tool()
def confirm_country_visit(country: str, visited: bool, source: str | None = None,
                          note: str | None = None) -> dict[str, Any]:
    """Record the traveler's own answer. True -> `confirmed` and counts.
    False -> `asked` and does not count. This is the only path to a counted visit.
    """
    with audit.timed("confirm_country_visit",
                     input_payload={"country": country, "visited": visited}) as ctx:
        led = ledger_mod.load().confirm(country, visited=visited, source=source, note=note)
        written = ledger_mod.save(led)
        record = led.get(country)
        out = {**written, "record": record.to_dict() if record else None,
               "count_visited": led.count_visited()}
        ctx["output"] = {"country": country, "visited": bool(record and record.visited)}
        return out


@mcp.tool()
def get_been_there_ledger() -> dict[str, Any]:
    """Read the been-there ledger: every row, the confirmed count, and the
    unconfirmed rows worth asking about."""
    with audit.timed("get_been_there_ledger", input_payload={}) as ctx:
        led = ledger_mod.load()
        out = led.to_dict()
        ctx["output"] = {"records": len(led.records), "visited": led.count_visited()}
        return out


# ----------------------------- ENTRY ELIGIBILITY -----------------------------

@mcp.tool()
def set_entry_eligibility(country: str, state: str, source: str | None = None,
                          as_of: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Record entry eligibility in one of THREE states.

    verified-eligible | verified-ineligible | unchecked

    Both verified states REQUIRE a source and an as-of date — a verification whose
    provenance was not recorded is not a verification. as_of defaults to today.
    """
    with audit.timed("set_entry_eligibility",
                     input_payload={"country": country, "state": state,
                                    "source": V.truncate(source or "", 200)}) as ctx:
        store = eligibility_mod.load().set_state(country, state, source=source,
                                                 as_of=as_of, note=note)
        written = eligibility_mod.save(store)
        out = {**written, "record": store.get(country).to_dict()}
        ctx["output"] = {"country": country, "state": store.get(country).state}
        return out


@mcp.tool()
def get_entry_eligibility(countries: list[str] | None = None) -> dict[str, Any]:
    """DISPLAY view: every state including `unchecked`.

    A UI may render unchecked as unchecked — hiding it would make an unknown look
    like a no. To decide what a router may PROPOSE, use filter_proposable_countries.
    """
    with audit.timed("get_entry_eligibility",
                     input_payload={"countries": countries}) as ctx:
        store = eligibility_mod.load()
        rows = store.display(countries)
        ctx["output"] = {"count": len(rows)}
        return {"count": len(rows), "records": rows}


@mcp.tool()
def filter_proposable_countries(countries: list[str],
                                max_age_days: int | None = None) -> dict[str, Any]:
    """ROUTER view: split candidates into proposable and refused.

    Only `verified-eligible` is proposable. `unchecked` is not a soft yes, and it
    is refused with reason "unchecked". Passing max_age_days also refuses a
    verification older than that, with reason "stale-verification".
    """
    with audit.timed("filter_proposable_countries",
                     input_payload={"countries": countries, "max_age_days": max_age_days}) as ctx:
        result = eligibility_mod.load().filter_proposable(countries, max_age_days=max_age_days)
        ctx["output"] = {"proposable": result["proposable_count"],
                         "refused": result["refused_count"]}
        return result


# ------------------------------- PREREQUISITES -------------------------------

@mcp.tool()
def save_trip_prerequisites(trip_slug: str,
                            prerequisites: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the prerequisite graph for a trip.

    Each entry: {key, kind, label, lead_days, hard_cutoff_days, country, depends_on}.
    kind ∈ visa-in-advance | vaccination | permit | document | booking.

      lead_days         how long the process takes once started
      hard_cutoff_days  must be COMPLETE this many days before departure
      depends_on        keys that must complete before this one can start

    A dependency cycle or an unknown key is rejected at write time, so an
    unschedulable graph never lands on disk.
    """
    with audit.timed("save_trip_prerequisites",
                     input_payload={"trip_slug": trip_slug,
                                    "count": len(prerequisites or [])}) as ctx:
        written = prereqs_mod.save(trip_slug, prerequisites)
        stored = prereqs_mod.load(trip_slug)
        out = {**written, "prerequisites": [p.to_dict() for p in stored]}
        ctx["output"] = {"trip_slug": trip_slug, "count": len(stored)}
        return out


@mcp.tool()
def prerequisite_schedule(
    trip_slug: str,
    departure: str,
    today: str | None = None,
    warn_days: int = 7,
) -> dict[str, Any]:
    """Schedule the saved prerequisites BACKWARD from departure.

    complete_by = min(departure - hard_cutoff_days, earliest start of anything
    that depends on this); start_by = complete_by - lead_days. Sorted by urgency,
    each row marked overdue | due-soon | on-track against `today`.
    """
    with audit.timed("prerequisite_schedule",
                     input_payload={"trip_slug": trip_slug, "departure": departure,
                                    "warn_days": warn_days}) as ctx:
        stored = prereqs_mod.load(trip_slug)
        scheduled = prereqs_mod.schedule_backward(
            stored, departure, today=today, warn_days=warn_days,
        )
        departure_iso = V.coerce_iso_date(departure, field="departure")
        out = {"trip_slug": V.validate_slug(trip_slug),
               **prereqs_mod.summarize(scheduled, departure=departure_iso)}
        ctx["output"] = {"trip_slug": trip_slug, "count": len(scheduled),
                         "overdue": len(out["overdue"])}
        return out


if __name__ == "__main__":
    mcp.run()
