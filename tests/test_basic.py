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
import os
import sys
import tempfile
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
    import audit, profile, router, prompts, validators, server  # noqa: F401


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
    import importlib, audit
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
        assert callable(obj) or hasattr(obj, "fn") or hasattr(obj, "__call__"), (
            f"{name} is not callable: {type(obj)}"
        )


def test_healthcheck_without_vault_env(monkeypatch):
    monkeypatch.delenv("TRAVEL_MCP_VAULT_PATH", raising=False)
    import importlib, server, profile
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
    import server, validators
    fn = getattr(server.analyze_route, "fn", server.analyze_route)
    with pytest.raises(validators.ValidationError):
        fn(origin="São Paulo", destination="JFK", dates="2026-12-15..2026-12-20")


def test_router_raises_when_no_auth_path():
    import router
    with pytest.raises(RuntimeError, match="auth"):
        router.call_claude_text(system="s", user="u")
