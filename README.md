# travel-mcp

> Analytical travel-planner MCP. Vault-backed profile + trip plans, seven flight-strategy analyzers, trip-prep + emergency-card + post-trip review. **Does not book.** Composes with Claude in Chrome or a flight-search MCP for real booking.

[![PyPI](https://img.shields.io/pypi/v/travel-mcp.svg)](https://pypi.org/project/travel-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Model Context Protocol server that turns Claude into a strategic flight + trip analyst. The 7 analyzers are grounded in the same fare-economics + revenue-management logic professional pricing analysts use. Every estimate is flagged for verification — this MCP doesn't fake live pricing.

## What it does

**21 tools across 4 surfaces:**

### Profile (vault-backed, no LLM)
- `healthcheck` — verify config, auto-create folder tree, seed Profile.md
- `get_travel_profile` — read your master profile
- `update_travel_profile_section` — update one section
- `list_companion_profiles` / `get_companion_profile` / `upsert_companion_profile` — partner / family / travel-buddy profiles

### Trip-plan persistence (vault-backed, no LLM)
- `save_trip_plan` — write analyzer output to `🧳 Travel/Trips/<slug>.md`
- `list_trip_plans` — filter by year + destination substring
- `get_trip_plan` — re-read a saved plan

### Seven flight-strategy analyzers (LLM via Claude Max)
- `analyze_route` — 5-category cheaper-alternative ranking (direct, hidden-city w/ legitimacy flags, nearby airports, multi-leg self-transfer, open-jaw/stopover)
- `pricing_reality_check` — confirmed mechanisms vs plausible-unconfirmed vs myths; grounded search method
- `geo_pricing_arbitrage` — POS arbitrage across home / origin / destination / third-country with legal booking methods
- `timing_sweet_spot` — when to book + when to fly; evidence-backed vs anecdotal patterns
- `fare_rules_analysis` — fare basis codes + 3-tier classification (legitimate / against-T&Cs / off-limits)
- `channel_comparison` — airline direct vs major OTA vs aggressive-pricing OTA vs consolidator
- `tracking_strategy` — fare-alert design with decision rules + backstops

### Five PDF-derived workflow tools (LLM via Claude Max)
- `trip_prep_brief` — one-page pre-departure brief (weather + packing + restaurants + meetings + ground transport)
- `emergency_travel_card` — single-page emergency reference (numbers, contingency plans, practical info)
- `compare_trips` — side-by-side trip A vs B with recommendation + tiebreaker
- `post_trip_review` — spending breakdown + points earned + profile-update suggestions
- `price_drop_analysis` — post-booking fare-drop monitor with carrier-specific rebooking steps

## What it does NOT do

- **No real flight search.** This MCP analyzes strategy. For live fares, compose with [`salamentic/google-flights-mcp`](https://github.com/salamentic/google-flights-mcp), [`ravinahp/flights-mcp`](https://github.com/ravinahp/flights-mcp), or Claude in Chrome.
- **No booking.** Booking requires payment + identity flow; out of scope.
- **No fabricated prices.** Every numeric estimate is flagged "verify before trusting." The LLM does not have live pricing data and the MCP enforces that discipline.

## Install

```bash
# 1. Clone or pip install
pip install travel-mcp
# OR
git clone https://github.com/adelaidasofia/travel-mcp.git ~/.claude/travel-mcp

# 2. Register in your project .mcp.json (or ~/.claude.json via `claude mcp add -s user`)
```

```json
{
  "mcpServers": {
    "travel": {
      "command": "python3",
      "args": ["/absolute/path/to/travel-mcp/server.py"],
      "env": {
        "TRAVEL_MCP_VAULT_PATH": "/absolute/path/to/your/vault"
      }
    }
  }
}
```

3. Restart Claude Code. Run `healthcheck` once to auto-create `🧳 Travel/` + seed `Profile.md`.
4. Fill in `Profile.md` (loyalty IDs, cabin rules, hotel chains, hard booking rules). The analyzers read this as the source of truth.

See [SETUP.md](SETUP.md) for the full first-time flow.

## Safety model

- **No `draft+confirm`** — this MCP is analysis, not destructive prod mutation.
- **No daily USD cap** — uses Claude Max subscription via CLI by default (zero per-token cost). Falls through to `ANTHROPIC_API_KEY` only when CLI is unavailable.
- **4-field observability JSONL** at `~/.claude/travel-mcp/audit.log.jsonl`. Per call: `execution_time_ms`, `io`, `token_usage`, `error_class`.
- **`sanitize_error()`** strips API keys, Bearer tokens, password patterns from every error/output before the seam.
- **INPUT-rail validation** rejects non-IATA airport strings, malformed dates, unknown cabin classes, and dangerous slugs before any LLM call.
- **Prompt-caching marker** on every system block (free; API no-ops when below model minimum).

## Voice + zero-hallucination discipline

The analyzer prompts enforce:
- Every price + percentage is flagged as an estimate to verify.
- Every "studies show…" claim is rated STRONG / MIXED / WEAK / DEBUNKED with citations.
- Every fare-rule tactic is classified LEGITIMATE / AGAINST-T&Cs / OFF-LIMITS.
- No menu-mode framing — analyzers pick rankings and recommendations, not "want me to do A or B?"

If the LLM doesn't know a price range for an obscure route, it says so. The MCP does not encourage fabrication.

## Example session

```
You: analyze_route from GRU to JFK, 2026-12-15 to 2026-12-22, 1 passenger,
     business class, flexibility ±3 days, risk tolerance medium.

Claude (via travel-mcp):
  1. BASELINE — direct AA / LATAM round-trip, business: $2,400-3,200 est.
  2. HIDDEN-CITY — NOT recommended (your profile shows status on LATAM).
  3. NEARBY AIRPORTS — EWR/LGA add ~$200 ground; GRU has no realistic substitute.
  4. SELF-TRANSFER — GRU→PTY→JFK saves $400-600 est. but no through-protection.
                     Minimum recommended layover: 4h in PTY.
  5. OPEN-JAW — fly into JFK, out of LGA: no meaningful saving on this route.

  TOP 3 FOR MEDIUM RISK:
  • Direct AA business, $2,400 est. — your AAdvantage status + lounge access
  • Direct LATAM business, $2,600 est. — your loyalty number, Oneworld lounges
  • Self-transfer via PTY, $2,000 est. — only if you can absorb a missed-connection
    night in PTY (Copa hotel ~$160/night)

  Every figure is an estimate. Verify on the day at the airline's site.
```

## Documentation

- [SETUP.md](SETUP.md) — first-time configuration + master profile template walkthrough
- [CHANGELOG.md](CHANGELOG.md) — release history
- [Claude Travel Agent Guide](https://mavgpt.ai) (MaverickAI) — the Cowork + Chrome approach this MCP composes with

## Companion stack

This MCP composes with:
- **Claude in Chrome** — the browser layer that actually navigates booking sites
- **[`salamentic/google-flights-mcp`](https://github.com/salamentic/google-flights-mcp)** — live flight search via fast-flights
- **[`ravinahp/flights-mcp`](https://github.com/ravinahp/flights-mcp)** — Duffel-API flight search
- **google-workspace MCP** — calendar events + Google Drive trip docs

## License

MIT. See [LICENSE](LICENSE).

---

Built by Adelaida Diaz-Roa. Full install or team version at [diazroa.com](https://diazroa.com).
