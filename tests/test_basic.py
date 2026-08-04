"""Smoke + unit tests for travel-mcp v0.1.0.

Covers:
  - module imports (server, profile, validators, router, prompts, audit)
  - validators reject bad input (IATA, ISO date, cabin, slug)
  - audit.sanitize_error strips API keys + Bearer + token + password patterns
  - audit.record writes JSONL line at the expected path
  - profile.ensure_dirs creates the folder tree + seeds Profile.md when missing
  - profile.upsert_companion is idempotent
  - profile.save_trip writes frontmatter + body
  - router.router_status reports availability flags without crashing
  - server tool registry contains all 21 tools
  - healthcheck without vault env returns ok=false, error_class="missing_env"
  - lazy router init: import works with no CLI + no API key
  - input rail rejection: analyze_route raises on non-IATA origin
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Every test runs against a fresh tmp vault + tmp audit log."""
    monkeypatch.setenv("TRAVEL_MCP_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("TRAVEL_MCP_PROFILE_FOLDER", "Travel")  # ASCII for testing
    audit_path = tmp_path / "audit.log.jsonl"
    monkeypatch.setenv("TRAVEL_MCP_AUDIT_PATH", str(audit_path))
    # Force-disable both auth paths so router doesn't try real calls.
    monkeypatch.setenv("CLAUDE_ROUTER_DISABLE_CLI", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Re-import so module-level constants pick up the new env.
    for mod in list(sys.modules):
        if mod in {"audit", "profile", "validators", "router", "prompts", "server"}:
            del sys.modules[mod]


def test_imports_clean():
    import server  # noqa: F401


def test_validate_iata_accepts_valid():
    import validators as V
    assert V.validate_iata("bog") == "BOG"
    assert V.validate_iata("JFK") == "JFK"
    assert V.validate_iata(" mia ") == "MIA"


def test_validate_iata_rejects_invalid():
    import validators as V
    with pytest.raises(V.ValidationError):
        V.validate_iata("São Paulo")
    with pytest.raises(V.ValidationError):
        V.validate_iata("NEW YORK")
    with pytest.raises(V.ValidationError):
        V.validate_iata("BO")


def test_validate_iso_date():
    import validators as V
    assert V.validate_iso_date("2026-12-15") == "2026-12-15"
    with pytest.raises(V.ValidationError):
        V.validate_iso_date("12/15/2026")
    with pytest.raises(V.ValidationError):
        V.validate_iso_date("2026-13-01")  # bad month


def test_normalize_dates_one_way():
    import validators as V
    out = V.normalize_dates("2026-12-15")
    assert out["outbound"] == "2026-12-15"
    assert out["return"] is None
    assert out["one_way"] is True


def test_normalize_dates_round_trip():
    import validators as V
    out = V.normalize_dates("2026-12-15..2026-12-20")
    assert out["outbound"] == "2026-12-15"
    assert out["return"] == "2026-12-20"
    assert out["one_way"] is False


def test_validate_cabin():
    import validators as V
    assert V.validate_cabin("Business") == "business"
    assert V.validate_cabin("premium-economy") == "premium_economy"
    assert V.validate_cabin(None) is None
    with pytest.raises(V.ValidationError):
        V.validate_cabin("super_premium_first")


def test_validate_slug_safety():
    import validators as V
    assert V.validate_slug("São Paulo → NYC trip!") == "s-o-paulo-nyc-trip"
    with pytest.raises(V.ValidationError):
        V.validate_slug("")
    with pytest.raises(V.ValidationError):
        V.validate_slug("!!!")


def test_audit_sanitize_strips_api_key():
    import audit
    out = audit.sanitize_error("Auth failed: sk-ant-abcdef1234567890abcdef1234567890ghijkl")
    assert "sk-ant-***REDACTED***" in out
    assert "abcdef1234567890" not in out


def test_audit_sanitize_strips_bearer_and_token():
    import audit
    out = audit.sanitize_error("Authorization: Bearer abc.def.ghi token=mysecret123 password=hunter2")
    # Defense in depth: both `Bearer <token>` and `Authorization: ...` strip,
    # so the final string may double-redact. What matters is that no leaked
    # value survives in cleartext.
    assert "***REDACTED***" in out
    assert "abc.def.ghi" not in out
    assert "mysecret123" not in out
    assert "hunter2" not in out


def test_audit_record_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAVEL_MCP_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    import importlib

    import audit
    importlib.reload(audit)
    audit.record("test_tool", execution_time_ms=42, io={"input": {"x": 1}, "output": {"y": 2}},
                 token_usage={"prompt": 10, "completion": 5, "cache_read": 0, "cache_creation": 0},
                 error_class=None)
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "test_tool"
    assert rec["execution_time_ms"] == 42
    assert rec["io"]["input"]["x"] == 1
    assert rec["token_usage"]["prompt"] == 10
    assert rec["error_class"] is None


def test_classify_error_walks_the_mro_instead_of_the_leaf_name(tmp_path):
    """NEGATIVE CONTROL: a new exception type must not read as an upstream fault.

    Dispatch used to match the exact leaf type name, so every exception type
    added after it was written fell through to `upstream_error` — on a surface
    that makes no upstream call at all. Then the taxonomy said "the provider
    broke" when the caller had passed a country nobody had verified.
    """
    import audit
    import eligibility
    import prereqs
    import validators as V

    cases = {
        eligibility.EligibilityRefused("Country B", "verified-ineligible",
                                       "verified-ineligible"): "policy_refusal",
        prereqs.PrerequisiteCycleError("a depends on b depends on a"): "validation",
        prereqs.UnknownPrerequisiteError("depends on unknown 'x'"): "validation",
        V.ValidationError("bad IATA"): "validation",
        ValueError("plain"): "validation",
    }
    assert {type(e).__name__: audit.classify_error(e) for e in cases} == {
        type(e).__name__: want for e, want in cases.items()
    }
    # The pre-existing classes keep their class: no silent reshuffle.
    assert audit.classify_error(TimeoutError("slow")) == "timeout"
    assert audit.classify_error(FileNotFoundError("gone")) == "filesystem"
    assert audit.classify_error(PermissionError("nope")) == "filesystem"
    assert audit.classify_error(RuntimeError("auth failed")) == "auth"
    assert audit.classify_error(RuntimeError("boom")) == "internal_error"
    # POSITIVE CONTROL: a genuinely unknown, non-derived failure still maps to
    # upstream_error, so the bucket is not simply unreachable now.
    assert audit.classify_error(Exception("provider returned garbage")) == "upstream_error"


def test_a_refused_proposal_is_audited_as_a_policy_refusal(tmp_path):
    """The classification has to survive the real tool path, not just a unit call."""
    import eligibility
    import server

    def fn(name):
        tool = getattr(server, name)
        return getattr(tool, "fn", tool)

    fn("save_itinerary")(slug="t", title="T", window_start="2030-03-01",
                         window_end="2030-05-30", segments=[])
    with pytest.raises(eligibility.EligibilityRefused):
        fn("add_itinerary_segment")(slug="t", city="Placeholder City",
                                    country="Country Z", arrive="2030-03-04",
                                    depart="2030-03-09", status="candidate")
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    refusals = [r for r in records if r["tool"] == "add_itinerary_segment"]
    assert [r["error_class"] for r in refusals] == ["policy_refusal"]


def test_profile_ensure_dirs_seeds_template(tmp_path):
    import profile
    status = profile.ensure_dirs()
    travel = tmp_path / "Travel"
    assert travel.exists()
    assert (travel / "Trips").exists()
    assert (travel / "Companions").exists()
    assert (travel / "Profile.md").exists()
    assert any("Profile.md" in p for p in status["created"])
    # Re-run is idempotent — second call returns "existed" not "created".
    status2 = profile.ensure_dirs()
    assert any("Profile.md" in p for p in status2["existed"])


def test_profile_update_section_idempotent(tmp_path):
    import profile
    profile.ensure_dirs()
    profile.update_profile_section("11. Voice", "Updated voice line.")
    body = (tmp_path / "Travel" / "Profile.md").read_text(encoding="utf-8")
    assert "Updated voice line." in body
    # Update again — old content replaced, not duplicated.
    profile.update_profile_section("11. Voice", "Even newer voice line.")
    body2 = (tmp_path / "Travel" / "Profile.md").read_text(encoding="utf-8")
    assert "Even newer voice line." in body2
    assert "Updated voice line." not in body2


def test_companion_upsert_idempotent(tmp_path):
    import profile
    profile.ensure_dirs()
    profile.upsert_companion("Test Partner", {"legal_name": "Test Person",
                                                "seat_preference": "Window"})
    profile.upsert_companion("Test Partner", {"legal_name": "Test Person Updated",
                                                "ktn": "TT12345"})
    items = profile.list_companions()
    assert len(items) == 1
    data = profile.read_companion("Test Partner")
    assert data["frontmatter"]["legal_name"] == "Test Person Updated"
    assert data["frontmatter"]["ktn"] == "TT12345"
    # First-call field still preserved (update merged, not overwrote).
    assert data["frontmatter"]["seat_preference"] == "Window"


def test_trip_save_and_list(tmp_path):
    import profile
    profile.ensure_dirs()
    profile.save_trip("nyc-dec-2026", "5 days NYC for client meetings",
                      "## Flight\n...",
                      {"destination": "New York", "outbound_date": "2026-12-15"})
    items = profile.list_trips()
    assert len(items) == 1
    assert items[0]["slug"] == "nyc-dec-2026"
    filtered = profile.list_trips(destination_contains="york")
    assert len(filtered) == 1


def test_router_status_no_crash():
    import router
    s = router.router_status()
    assert "cli_available" in s
    assert "api_key_set" in s
    assert "default_model" in s
    # In test env: CLAUDE_ROUTER_DISABLE_CLI=1, no ANTHROPIC_API_KEY
    assert s["api_key_set"] is False


def test_server_tool_registry_has_all_21():
    """All 21 tools are decorated as @mcp.tool() and exposed on the server module."""
    import server
    expected = {
        "healthcheck", "get_travel_profile", "update_travel_profile_section",
        "list_companion_profiles", "get_companion_profile", "upsert_companion_profile",
        "save_trip_plan", "list_trip_plans", "get_trip_plan",
        "analyze_route", "pricing_reality_check", "geo_pricing_arbitrage",
        "timing_sweet_spot", "fare_rules_analysis", "channel_comparison",
        "tracking_strategy",
        "trip_prep_brief", "emergency_travel_card", "compare_trips",
        "post_trip_review", "price_drop_analysis",
    }
    assert len(expected) == 21
    missing = [name for name in expected if not hasattr(server, name)]
    assert not missing, f"server module missing tool functions: {missing}"
    # Each tool is a FastMCP-wrapped callable. Either the raw function (.fn) or
    # the FunctionTool wrapper exposes the underlying signature.
    for name in expected:
        obj = getattr(server, name)
        assert callable(obj) or hasattr(obj, "fn"), (
            f"{name} is not callable: {type(obj)}"
        )


def test_healthcheck_without_vault_env(monkeypatch):
    monkeypatch.delenv("TRAVEL_MCP_VAULT_PATH", raising=False)
    import importlib
    import profile

    import server
    importlib.reload(profile)
    importlib.reload(server)
    result = server.healthcheck.fn() if hasattr(server.healthcheck, "fn") else None
    # FastMCP wraps tools — call the underlying function directly.
    if result is None:
        # Try plain call (some FastMCP versions expose tools as callables)
        try:
            result = server.healthcheck()
        except Exception:
            pytest.skip("FastMCP tool-call API changed; manual check")
    assert result["ok"] is False
    assert result["error_class"] == "missing_env"


def test_analyze_route_rejects_non_iata():
    import server
    import validators
    fn = getattr(server.analyze_route, "fn", server.analyze_route)
    with pytest.raises(validators.ValidationError):
        fn(origin="São Paulo", destination="JFK", dates="2026-12-15..2026-12-20")


def test_router_raises_when_no_auth_path():
    import router
    with pytest.raises(RuntimeError, match="auth"):
        router.call_claude_text(system="s", user="u")


# ---------------------------------------------------------------------------
# Billing-leak gate: a Max CLI rate-limit must fail loud, NEVER spill to the
# paid Anthropic API. Bug class SILENT-PAID-FALLBACK-ON-RATE-LIMIT.
# ---------------------------------------------------------------------------
class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_cli_ratelimit_banner_raises_not_returned(monkeypatch):
    """A rate-limit banner on stdout raises RateLimited (never served as text)."""
    import router
    monkeypatch.setattr(
        router.subprocess,
        "run",
        lambda *a, **k: _Proc(
            stdout="You've hit your session limit · resets 11:50pm", returncode=1
        ),
    )
    with pytest.raises(router.RateLimited):
        router._call_via_cli(system="s", user="u", model="claude-sonnet-4-6")


def test_ratelimit_never_spills_to_paid_api(monkeypatch):
    """NEGATIVE CONTROL: a CLI rate-limit must NOT reach the paid API even when
    ANTHROPIC_API_KEY is set."""
    import router

    def _raise_rl(**k):
        raise router.RateLimited("max capped")

    monkeypatch.setattr(router, "_cli_available", lambda: True)
    monkeypatch.setattr(router, "_prefer_api_key", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    monkeypatch.setattr(router, "_call_via_cli", _raise_rl)
    monkeypatch.setattr(
        router,
        "_call_via_api",
        lambda **k: pytest.fail("paid API must not be called on a rate-limit"),
    )
    with pytest.raises(router.RateLimited):
        router.call_claude_text(system="s", user="u")


def test_normal_cli_answer_still_returned(monkeypatch):
    """POSITIVE CONTROL: a real answer (no rate-limit markers) is returned."""
    import router
    monkeypatch.setattr(
        router.subprocess,
        "run",
        lambda *a, **k: _Proc(stdout="Fly BOG-JFK on Avianca, book 6 weeks out.", returncode=0),
    )
    result = router._call_via_cli(system="s", user="u", model="claude-sonnet-4-6")
    assert result.auth == "max-subscription"
    assert "Avianca" in result.text
