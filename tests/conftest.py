"""Shared fixtures for the itinerary-model tests.

`travel_vault` is deliberately NOT autouse: tests/test_basic.py ships its own
autouse isolation fixture, and stacking two would make it unclear which one a
failure came from. New tests request this one by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Purged before and after each test so no module keeps a handle on a tmp vault.
TRAVEL_MODULES = (
    "audit", "profile", "validators", "router", "prompts", "server",
    "itinerary", "preferences", "ledger", "dwell", "eligibility", "prereqs",
    # `booking` binds validators at import time. Reloading validators without
    # reloading booking leaves two distinct ValidationError classes alive and
    # `pytest.raises` silently stops matching. It travels with validators.
    "booking",
)


def _purge() -> None:
    for name in TRAVEL_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def travel_vault(tmp_path, monkeypatch):
    """A fresh tmp vault with both auth paths disabled."""
    monkeypatch.setenv("TRAVEL_MCP_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("TRAVEL_MCP_PROFILE_FOLDER", "Travel")  # ASCII for testing
    monkeypatch.setenv("TRAVEL_MCP_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAUDE_ROUTER_DISABLE_CLI", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _purge()
    yield tmp_path
    _purge()
