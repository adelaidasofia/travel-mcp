#!/usr/bin/env python3
"""Eval-gate harness for travel-mcp v0.1.0.

Walks tests/eval_fixtures/*.yaml, calls each tool n_samples times, verifies:
  1. every expected_substrings_any_case appears in EACH sample (case-insensitive)
  2. no expected_absent_case_sensitive appears in ANY sample (privacy/safety)
  3. self-consistency: majority of samples pass 1+2

CI behavior:
  - Default: requires either `claude` CLI (Max) OR ANTHROPIC_API_KEY env.
  - If neither, AND `TRAVEL_MCP_EVAL_SKIP_OK=1` is set, prints SKIPPED + exits 0.
  - Without the skip flag, exits 1 to fail loud.

Per ⚙️ Meta/rules/eval-gates-in-ci.md: "ground-truth fixtures (≥5 per endpoint),
self-consistency sampling (N≥3, vote, fail on variance), multi-agent review across
≥2 model families. CI step BLOCKS merge, not advisory."

v0.1.0 ships Tier-1 Claude single-family. Multi-family deferred to v0.2 with
`--families` flag. Track in CHANGELOG.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _has_auth() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_ROUTER_DISABLE_CLI") == "1":
        return False
    return shutil.which("claude") is not None


def _resolve_tool(server_module, name: str):
    """FastMCP wraps @mcp.tool() functions. Find the underlying callable."""
    obj = getattr(server_module, name, None)
    if obj is None:
        raise RuntimeError(f"server module missing tool: {name}")
    if hasattr(obj, "fn") and callable(obj.fn):
        return obj.fn
    if callable(obj):
        return obj
    raise RuntimeError(f"{name} is not callable: {type(obj)}")


def _check_sample(text: str, expected_any_case: list[str],
                  expected_absent_case: list[str]) -> list[str]:
    """Return list of issue strings; empty == pass."""
    issues: list[str] = []
    lower = text.lower()
    for needle in expected_any_case or []:
        if needle.lower() not in lower:
            issues.append(f"missing expected substring (any-case): {needle!r}")
    for needle in expected_absent_case or []:
        if needle in text:
            issues.append(f"forbidden substring present (case-sensitive): {needle!r}")
    return issues


def run_one_fixture(server_module, fixture: dict) -> tuple[bool, list[str]]:
    name = fixture.get("name", "<unnamed>")
    tool_name = fixture["tool"]
    inp = fixture.get("input", {}) or {}
    n = int(fixture.get("n_samples", 3))
    expected_any = fixture.get("expected_substrings_any_case", [])
    expected_absent = fixture.get("expected_absent_case_sensitive", [])

    fn = _resolve_tool(server_module, tool_name)
    sample_results: list[tuple[bool, list[str]]] = []
    for i in range(n):
        try:
            result = fn(**inp)
            text = (result or {}).get("text", "") if isinstance(result, dict) else str(result)
        except Exception as exc:
            sample_results.append((False, [f"sample {i+1}: tool raised {type(exc).__name__}: {exc}"]))
            continue
        issues = _check_sample(text, expected_any, expected_absent)
        sample_results.append((not issues, issues))

    passes = sum(1 for ok, _ in sample_results if ok)
    majority_needed = (n // 2) + 1
    overall_ok = passes >= majority_needed

    log_lines: list[str] = []
    log_lines.append(f"  [{tool_name}/{name}] {passes}/{n} samples passed "
                     f"(majority needed: {majority_needed}) — {'OK' if overall_ok else 'FAIL'}")
    if not overall_ok:
        for i, (ok, issues) in enumerate(sample_results):
            if not ok:
                for issue in issues:
                    log_lines.append(f"    sample {i+1}: {issue}")
    return overall_ok, log_lines


def main() -> int:
    fixtures_dir = Path(__file__).resolve().parent
    yaml_files = sorted(fixtures_dir.glob("*.yaml"))
    if not yaml_files:
        print("no eval fixtures found at", fixtures_dir)
        return 1

    if not _has_auth():
        if os.environ.get("TRAVEL_MCP_EVAL_SKIP_OK") == "1":
            print("SKIPPED: no Claude auth path available "
                  "(TRAVEL_MCP_EVAL_SKIP_OK=1, returning 0)")
            return 0
        print("FAIL: no Claude auth path. Either:")
        print("  - install + login the `claude` CLI (Max plan), or")
        print("  - set ANTHROPIC_API_KEY")
        print("  - set TRAVEL_MCP_EVAL_SKIP_OK=1 to skip in CI without credentials")
        return 1

    if not os.environ.get("TRAVEL_MCP_VAULT_PATH"):
        # Use a tmpdir so profile.read_profile() doesn't crash inside trip_prep_brief etc.
        import tempfile
        tmp = tempfile.mkdtemp(prefix="travel-mcp-eval-")
        os.environ["TRAVEL_MCP_VAULT_PATH"] = tmp
        os.environ.setdefault("TRAVEL_MCP_PROFILE_FOLDER", "Travel")

    import server
    fixture_count = 0
    tool_count_passed = 0
    tool_count_total = 0
    all_logs: list[str] = []
    overall_ok = True

    for yaml_file in yaml_files:
        all_logs.append(f"\n[{yaml_file.name}]")
        with yaml_file.open(encoding="utf-8") as f:
            fixtures = yaml.safe_load(f) or []
        if not isinstance(fixtures, list):
            all_logs.append(f"  SKIP: {yaml_file.name} not a list of fixtures")
            continue
        for fixture in fixtures:
            fixture_count += 1
            tool_count_total += 1
            ok, lines = run_one_fixture(server, fixture)
            all_logs.extend(lines)
            if ok:
                tool_count_passed += 1
            else:
                overall_ok = False

    print("\n".join(all_logs))
    print(f"\nEvalGate: {tool_count_passed}/{tool_count_total} fixtures passed "
          f"({fixture_count} total)")
    print("OK" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
