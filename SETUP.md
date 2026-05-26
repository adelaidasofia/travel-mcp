# travel-mcp setup

First-time configuration. After this you never have to re-enter your loyalty numbers, credit card rules, seat preferences, or hotel requirements again — every future trip starts with full context.

## 1. Install

```bash
git clone https://github.com/adelaidasofia/travel-mcp.git ~/.claude/travel-mcp
cd ~/.claude/travel-mcp
pip install -r requirements.txt   # OR `uv sync`
```

Verify:

```bash
python3 -c "import server; print('OK')"
```

Should print `OK`. If it errors, `pip install fastmcp httpx python-frontmatter pyyaml` and retry.

## 2. Pick your vault root

`travel-mcp` writes your profile + trip plans into an Obsidian-style vault. Set the absolute path:

```bash
export TRAVEL_MCP_VAULT_PATH="/Users/you/Vault"
```

By default it creates a `🧳 Travel/` folder at the vault root. Override with `TRAVEL_MCP_PROFILE_FOLDER`.

## 3. Register in Claude Code

Add to your project `.mcp.json` (loaded when cwd is inside the vault, including worktrees):

```json
{
  "mcpServers": {
    "travel": {
      "command": "python3",
      "args": ["/Users/you/.claude/travel-mcp/server.py"],
      "env": {
        "TRAVEL_MCP_VAULT_PATH": "/Users/you/Vault"
      }
    }
  }
}
```

Or user-scope (available in every project):

```bash
claude mcp add -s user travel python3 /Users/you/.claude/travel-mcp/server.py \
  --env TRAVEL_MCP_VAULT_PATH=/Users/you/Vault
```

**Restart Claude Code** so the new tools load.

Verify:

```bash
claude mcp get travel    # expect: Status: ✓ Connected
```

## 4. Run healthcheck once

In Claude Code, call:

```
travel.healthcheck()
```

This auto-creates:
- `<vault>/🧳 Travel/Profile.md` (master profile template — 11 sections)
- `<vault>/🧳 Travel/Trips/` (analyzer output lives here)
- `<vault>/🧳 Travel/Companions/` (partner / family / travel-buddy profiles)

Re-running healthcheck is idempotent — it returns `existed` for already-created paths.

## 5. Fill in your Profile.md

Open `<vault>/🧳 Travel/Profile.md` in Obsidian. Replace every `[FILL IN]` with your real data. Spend 15 minutes — you save that time on every single booking going forward.

### Sections in order

| # | Section | What goes here |
|---|---|---|
| 1 | Identity | Legal name, DOB, passport, KTN, Global Entry, Redress |
| 2 | Travel style | Priority order (schedule / time / loyalty / comfort / price / points) |
| 3 | Credit cards & payment strategy | Each card's last-4, best-for category, benefits, points programs + cpp values |
| 4 | Airports & flights | Primary airport, backup airports, seat / cabin / schedule rules, preferred airlines + tier |
| 5 | Hotels & stays | Style, must-haves, hotel chains in priority order, budget cap per night |
| 6 | Lounges & airport experience | TSA PreCheck, Global Entry, CLEAR, arrival buffer, lounge cards |
| 7 | Ground transportation | Uber default, rental loyalty, walking vs transit preference |
| 8 | Restaurants | Cuisines, style, budget per dinner, reservation platforms |
| 9 | International travel | Always-check checklist (passport validity, visa, insurance, eSIM) |
| 10 | Hard booking rules | NEVER book without approval. Always show alternatives + cancellation + card + loyalty |
| 11 | Voice | How you want Claude to talk to you |

### Field-by-field tip

For each preferred airline / hotel chain, the format is:

```
1. [AIRLINE] — ID: [XXXX] — Tier: [TIER]
```

Replace `[AIRLINE]` with the carrier name (e.g. American Airlines), `[XXXX]` with your loyalty number, `[TIER]` with your status (e.g. Gold, Diamond, Bonvoy Titanium). Order top-to-bottom by preference.

## 6. Add companion profiles (optional)

If you travel with a partner, family, or frequent buddy:

```
travel.upsert_companion_profile(
  name="Partner",
  legal_name="Alex Smith",
  date_of_birth="1985-04-12",
  passport="A12345678",
  passport_expiry="2029-06-30",
  ktn="TT12345",
  seat_preference="Aisle",
  airline_loyalty="American AAdvantage: 9876543210",
  hotel_loyalty="Marriott Bonvoy: 1234567"
)
```

The analyzers + trip-prep tools pull companion data when relevant.

## 7. First analyzer call

Try a route you actually care about:

```
travel.analyze_route(
  origin="BOG",
  destination="JFK",
  dates="2026-12-15..2026-12-22",
  passengers=1,
  cabin="business",
  flexibility="±3 days",
  risk_tolerance="medium"
)
```

Save the output to your vault:

```
travel.save_trip_plan(
  slug="nyc-dec-2026",
  summary="5 days NYC for client meetings, business cabin",
  content=<paste analyzer output>,
  destination="New York",
  outbound_date="2026-12-15",
  return_date="2026-12-22"
)
```

The trip plan lives at `<vault>/🧳 Travel/Trips/nyc-dec-2026.md`.

## 8. Trip prep (after booking)

```
travel.trip_prep_brief(
  destination="New York",
  dates="2026-12-15..2026-12-22",
  hotel="Marriott Marquis Times Square",
  purpose="business",
  vibe="efficient + comfortable"
)
```

One-page brief: weather + packing list + restaurants near hotel + meetings (pulled from calendar context if you provide it) + ground transport + what you might forget + money + one personal note.

## 9. Emergency card (international trips)

```
travel.emergency_travel_card(
  destination="Tokyo, Japan",
  dates="2026-12-15..2026-12-22",
  hotel="Park Hyatt Tokyo"
)
```

Screenshot to your phone or print to a card. Embassy + hospital + contingency plans + local phrases.

## 10. Post-trip review

```
travel.post_trip_review(
  destination="New York",
  dates="2026-12-15..2026-12-22",
  booking_data="<paste your booking confirmations / Amex statement>"
)
```

Catches "you should have used the hotel card not the flight card" mistakes that cost real points over a year.

## Authentication paths

The 12 LLM-backed tools (7 analyzers + 5 PDF-derived) use this auth cascade:

1. **Tier 1 — Claude via Max plan CLI** (default, zero per-token cost). Requires `claude` CLI installed + logged in.
2. **Tier 3 — Anthropic API key** (fallback). Set `ANTHROPIC_API_KEY` env var. Only consulted when CLI is unavailable OR `CLAUDE_ROUTER_PREFER_API_KEY=1` is set.

Tier 2 (NVIDIA) is intentionally skipped — travel analysis is judgment-heavy.

Force the fallback path for testing: `CLAUDE_ROUTER_DISABLE_CLI=1`.

## Audit log

Every tool call writes a 4-field JSONL line to `~/.claude/travel-mcp/audit.log.jsonl`:

```json
{"ts":"2026-12-15T09:42:11-05:00","tool":"analyze_route","execution_time_ms":2140,
 "io":{"input":{...},"output":{"text_chars":3210,"auth":"max-subscription"}},
 "token_usage":{"prompt":1200,"completion":340,"cache_read":800,"cache_creation":0},
 "error_class":null}
```

Tail it during a session:

```bash
tail -f ~/.claude/travel-mcp/audit.log.jsonl | jq .
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `healthcheck` returns `ok=false, error_class=missing_env` | Set `TRAVEL_MCP_VAULT_PATH` in your `.mcp.json` env block or shell env |
| Tool call raises `auth: no Claude path available` | Either install + login `claude` CLI (Max) OR set `ANTHROPIC_API_KEY` |
| `analyze_route` raises ValidationError on "São Paulo" | Use the 3-letter IATA code, e.g. `GRU` not the city name |
| `dates` raises ValidationError | Use ISO format: `2026-12-15` or `2026-12-15..2026-12-22` |
| Profile.md has em dashes I don't want | Edit directly in Obsidian — the template is a starting point |
