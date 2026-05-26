# Changelog

All notable changes to travel-mcp will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] — 2026-05-26

### Fixed

- **LLM-router CLI timeout bumped 180s → 600s.** Real `analyze_route` calls (BOG→JFK, sonnet-4-6) routinely land 200-300s end-to-end via `claude -p` cold-start + reasoning. The 180s default in v0.1.0 hit the timeout on the first live invocation. Env override `TRAVEL_MCP_CLI_TIMEOUT` still honored.
- **Placeholder-profile detection now catches ALL bracket markers**, not just `[FILL IN]`. The seeded `Profile.md` template has ~50 placeholders across `[FILL IN]`, `[CARD NAME]`, `[XXXX]`, `[IATA]`, `[NUMBER]`, `[AIRLINE]`, `[CHAIN]`, `[TIER]`, `[COMPANY]`, etc. v0.1.0 counted only `[FILL IN]` (~8 in template) and missed the threshold, passing the full empty template into every LLM system block. v0.1.1 regex-matches `\[[A-Z][A-Z0-9 /\-_]+\]` and collapses to a 132-char marker when >5 placeholders remain. Saves ~30K context tokens per analyzer call on a fresh-template profile.

### Caught by

End-to-end self-test in the same session as v0.1.0 ship. `mcp__travel__analyze_route(BOG, JFK, ...)` timed out at 180s; out-of-band test with v0.1.1 changes returned 9,330 chars in 228.5s via `auth=max-subscription`. Verification-before-completion gate per CLAUDE.md auto-mode rule (verification gates never skipped, even in auto-mode).

## [0.1.0] — 2026-05-26

### Added

- Initial release. FastMCP stdio server with 21 tools across 4 surfaces.
- **Profile surface (6 tools)**: `healthcheck`, `get_travel_profile`, `update_travel_profile_section`, `list_companion_profiles`, `get_companion_profile`, `upsert_companion_profile`. Vault-backed at `<vault>/🧳 Travel/Profile.md` + `<vault>/🧳 Travel/Companions/<name>.md`.
- **Persistence surface (3 tools)**: `save_trip_plan`, `list_trip_plans`, `get_trip_plan`. Writes to `<vault>/🧳 Travel/Trips/<slug>.md` with structured frontmatter (destination, dates, total_cost_usd).
- **Seven flight-strategy analyzers**: `analyze_route` (5-category ranking with hidden-city legitimacy flags), `pricing_reality_check` (3-bucket confirmed/plausible/myth taxonomy), `geo_pricing_arbitrage` (POS arbitrage + legal booking methods), `timing_sweet_spot` (booking window + cheapest days), `fare_rules_analysis` (fare basis codes + 3-tier classification), `channel_comparison` (airline direct vs OTAs), `tracking_strategy` (fare-alert design with decision rules + backstops).
- **Five PDF-derived workflow tools**: `trip_prep_brief` (one-page pre-departure), `emergency_travel_card` (single-page emergency reference), `compare_trips` (side-by-side A vs B), `post_trip_review` (spending + points review), `price_drop_analysis` (post-booking monitor).
- **Auth cascade**: Claude Max CLI (Tier 1, zero per-token cost) → Anthropic API key (Tier 3 fallback). Tier 2 (NVIDIA) intentionally skipped per CLAUDE.md routing rules.
- **Safety model**: per-call 4-field observability JSONL (`execution_time_ms`, `io`, `token_usage`, `error_class`), `sanitize_error()` stripping 13 credential patterns, INPUT-rail validation (IATA + ISO date + cabin + slug + risk tolerance), prompt-caching marker on every LLM system block.
- **Documentation**: README with companion-stack pointers, SETUP.md with first-time flow + master profile template walkthrough, .env.example with all configurable env vars.

### Why this exists

The MaverickAI _Claude Travel Agent Guide_ describes a Claude Cowork + Chrome-connector approach to BOOKING travel. The 7 analytical prompts in the original user brief (route analyzer, pricing-reality, geo-pricing, timing sweet-spot, fare rules, channel comparison, fare tracking) are the analytical SUBSTRATE that informs booking decisions. This MCP encapsulates that substrate as reusable tools that:

1. Persist your travel profile + trip plans across sessions (vault, not chat history).
2. Run the 7 analyzers + 5 PDF-derived workflows as one-call tools instead of pasting prompts.
3. Compose with — don't duplicate — real flight-search MCPs (`salamentic/google-flights-mcp`, `ravinahp/flights-mcp`) and Claude in Chrome for the actual booking flow.

### Known limitations

- **No live pricing.** The LLM doesn't have real-time flight data. Every numeric estimate is flagged "verify before trusting" by the prompts.
- **No booking.** Booking requires payment + identity flow; out of scope. For real booking, compose with Claude in Chrome.
- **Eval gate scaffolded but not enforced.** v0.1.0 ships `tests/eval_fixtures/` with ground-truth fixtures + `run_evals.py` harness. Multi-family review (Tier-1 Claude + a second family) is deferred to v0.2.

### Existing-MCP audit

Per MCP Build Runbook Lesson #16, surveyed 12 existing travel/flights MCPs before building:

- `salamentic/google-flights-mcp` — wraps fast-flights API (real flight search). ~25% overlap (we don't search).
- `ravinahp/flights-mcp` — wraps Duffel API (real flight search). ~20% overlap.
- `HaroldLeo/google-flights-mcp` — 10 built-in prompts. ~30% overlap on prompt surface.
- `gs-ysingh/travel-mcp-server`, `Fieldy76/Agentic-Travel-Planner`, `skarlekar/mcp_travelassistant`, `GongRzhe/TRAVEL-PLANNER-MCP-Server`, `lev-corrupted/travel-mcp-server`, `prakashsanker/flights-mcp-server`, `ExpediaGroup/expedia-travel-recommendations-mcp`, `pratikjadhav2726/mcp-amadeusflights`, `ppiova/TravelMCP` — all wrap a real flight/hotel API.

**None are analyzer-only with vault-backed profile + 7-prompt strategic surface.** Build fresh per Lesson #16 decision tree (<50% match on closest candidate). This MCP composes with the API-wrapping siblings instead of duplicating them.
