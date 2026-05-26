# Eval fixtures for travel-mcp LLM tools

Per `⚙️ Meta/rules/eval-gates-in-ci.md`: every LLM-calling endpoint ships ground-truth fixtures + self-consistency sampling + multi-agent review.

## What's here

| File | Tool | N samples | What's checked |
|---|---|---|---|
| `analyze_route.yaml` | `analyze_route` | 3 | 5 category headers present, no privacy leak |
| `pricing_reality_check.yaml` | `pricing_reality_check` | 3 | Confirmed/Plausible/Myth labels present |
| `geo_pricing_arbitrage.yaml` | `geo_pricing_arbitrage` | 3 | POS comparison + legal-methods discipline |
| `timing_sweet_spot.yaml` | `timing_sweet_spot` | 3 | Booking window + cheapest day + action plan |
| `fare_rules_analysis.yaml` | `fare_rules_analysis` | 3 | 3-tier classification language |
| `channel_comparison.yaml` | `channel_comparison` | 3 | Channel-by-channel + decision rule |
| `tracking_strategy.yaml` | `tracking_strategy` | 3 | Tools + cadence + decision rule + worked example |
| `trip_prep_brief.yaml` | `trip_prep_brief` | 3 | 8 required sections + under-500-word discipline |
| `emergency_travel_card.yaml` | `emergency_travel_card` | 3 | Embassy + hospital + contingency + practical |
| `compare_trips.yaml` | `compare_trips` | 3 | A vs B + recommendation + tiebreaker |
| `post_trip_review.yaml` | `post_trip_review` | 3 | 5 required sections + under-400-word discipline |
| `price_drop_analysis.yaml` | `price_drop_analysis` | 3 | Current price + carrier policy + action plan |

## How to run

```bash
cd ~/.claude/travel-mcp
TRAVEL_MCP_VAULT_PATH=/path/to/vault python3 tests/eval_fixtures/run_evals.py
```

Requires the Claude Max CLI logged in (`claude` command available) OR `ANTHROPIC_API_KEY` set.

## What "pass" means

For each fixture:
1. Tool is called `n_samples` times with the same input.
2. Each output must contain every `expected_substring` (case-insensitive).
3. No output may contain any `expected_absent` substring (case-sensitive — privacy leaks).
4. Self-consistency: at least `floor(n_samples / 2) + 1` calls must satisfy 2+3 (majority vote).

Failures print: which fixture, which sample, missing/leaked substring, the offending output. CI step exits 1 on any failure.

## CI behavior

`publish-mcp.yml` runs `run_evals.py` with `TRAVEL_MCP_EVAL_SKIP_OK=1` set when CI doesn't have Claude credentials — the harness then exits 0 with a clear "SKIPPED: no auth" message. To actually enforce in CI, plumb an `ANTHROPIC_API_KEY` secret into the workflow and remove the skip flag.

## Multi-family review (deferred to v0.2)

The rule mandates ≥2 model families. v0.1.0 ships Tier-1 Claude only. v0.2 will add a Tier-2 family (NVIDIA Llama 4 / Qwen3) via the same harness's `--families` flag — each fixture rerun under each family, with vote across families.

## Adding fixtures

Each YAML file is a list of fixture entries. Add new entries for edge cases (one-way trips, premium cabin, long-haul international, etc.). The harness picks them up automatically.

## Privacy-guard placeholders + local override

The shipped fixtures use placeholder strings in `expected_absent_case_sensitive` (`PRIVATE_TRAVELER_FIRST_NAME`, `PRIVATE_COMPANY_CODENAME`, `PRIVATE_PROJECT_CODENAME`) that document the pattern without baking in any real names.

**To enforce privacy on your actual names**, create a local fixture file with real values:

```bash
mkdir -p tests/eval_fixtures.local
cp tests/eval_fixtures/analyze_route.yaml tests/eval_fixtures.local/analyze_route.local.yaml
# then edit the .local.yaml — replace the PRIVATE_* placeholders with your
# real names, company codenames, project codenames the LLM should never emit.
```

`tests/eval_fixtures.local/` and `*.local.yaml` are gitignored. The harness picks up fixtures from `tests/eval_fixtures/` only — to extend it to read both, pass `--also tests/eval_fixtures.local` (planned for v0.2).
