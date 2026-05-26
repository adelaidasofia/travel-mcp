"""Claude Max → API key router for travel-mcp LLM tools.

Mirrors ⚙️ Meta/scripts/_claude_router.py (the canonical vault helper). FastMCP
servers can't import from the vault, so this module is a self-contained copy of
the pattern.

Auth cascade:
  Tier 1: Claude via Max plan CLI    — default, zero per-token cost
  Tier 3: Anthropic API key (SDK)    — only when CLI is unavailable

Tier 2 (NVIDIA) is intentionally skipped: travel analysis is judgment-heavy
(hidden-city legitimacy, fare-rule tradeoffs, route comparisons) and the
CLAUDE.md Tier-2 carve-out explicitly bans NVIDIA for that class.

Prompt caching: cache_control marker on the system block per
⚙️ Meta/rules/prompt-caching.md. The Anthropic API silently no-ops when the
prefix is below the model minimum (1K Sonnet 4.5 / 4K Opus 4.7 / Sonnet 4.6),
so the marker is free and future-proofs each surface.

Tracing: returns {prompt, completion, cache_read, cache_creation} token counts
so audit.record() can populate token_usage in the 4-field schema. Per
⚙️ Meta/rules/llm-tracing-conventions.md, prompt/response CONTENT is NEVER
included in audit attributes — only model + token counts + latency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

DEFAULT_MODEL = os.environ.get("TRAVEL_MCP_MODEL", "claude-sonnet-4-6")
CLI_TIMEOUT_SECONDS = int(os.environ.get("TRAVEL_MCP_CLI_TIMEOUT", "180"))


@dataclass
class RouterResult:
    text: str
    auth: str           # "max-subscription" | "api-key" | "unknown"
    model: str
    token_usage: dict[str, int]
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "auth": self.auth,
            "model": self.model,
            "token_usage": self.token_usage,
            "elapsed_ms": self.elapsed_ms,
        }


def _cli_available() -> bool:
    if os.environ.get("CLAUDE_ROUTER_DISABLE_CLI") == "1":
        return False
    return shutil.which("claude") is not None


def _prefer_api_key() -> bool:
    return os.environ.get("CLAUDE_ROUTER_PREFER_API_KEY") == "1"


def _zero_usage() -> dict[str, int]:
    return {"prompt": 0, "completion": 0, "cache_read": 0, "cache_creation": 0}


def call_claude_text(
    system: str,
    user: str,
    *,
    model: str | None = None,
    cache_system: bool = True,
) -> RouterResult:
    """Run a single-shot Claude call. Returns text + auth + token counts.

    Best-of-best: Tier 1 (Max CLI) first. Falls through to Tier 3 (API key) only
    when CLI is unavailable or explicitly disabled.

    cache_system: if True, attaches `cache_control` to the system block on the
    Tier-3 path. The CLI path caches automatically per Max plan; nothing to do.
    """
    import time

    chosen_model = model or DEFAULT_MODEL
    start = time.perf_counter()

    cli_ok = _cli_available() and not _prefer_api_key()
    if cli_ok:
        try:
            cli_result = _call_via_cli(system=system, user=user, model=chosen_model)
            cli_result.elapsed_ms = int((time.perf_counter() - start) * 1000)
            return cli_result
        except RuntimeError:
            # CLI path failed; fall through to API key only if available.
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise

    api_result = _call_via_api(
        system=system, user=user, model=chosen_model, cache_system=cache_system,
    )
    api_result.elapsed_ms = int((time.perf_counter() - start) * 1000)
    return api_result


def _call_via_cli(*, system: str, user: str, model: str) -> RouterResult:
    """Tier 1 — claude -p print mode. Reads keychain OAuth, zero per-token cost.

    Tolerate non-zero exit code if stdout has ≥20 chars: SessionStart/SessionEnd
    hooks fire even in print mode, and a failing hook exits 1 AFTER Claude has
    written its response. Discarding a valid stdout because of hook noise is the
    bug class this tolerance fixes (per _claude_router.py).
    """
    cmd = [
        "claude", "-p", user,
        "--append-system-prompt", system,
        "--model", model,
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout or len(stdout) < 20:
        msg = stderr or "claude CLI returned empty stdout"
        raise RuntimeError(f"max-subscription path failed: {msg[:400]}")
    return RouterResult(
        text=stdout,
        auth="max-subscription",
        model=model,
        token_usage=_zero_usage(),  # CLI does not emit usage; subscription billing
        elapsed_ms=0,
    )


def _call_via_api(*, system: str, user: str, model: str, cache_system: bool) -> RouterResult:
    """Tier 3 — Anthropic SDK fallback. Only when CLI unavailable."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "auth: no Claude path available. Either install + login the `claude` CLI "
            "(Max plan) OR set ANTHROPIC_API_KEY for the fallback path."
        )
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "auth: ANTHROPIC_API_KEY set but `anthropic` SDK not installed. "
            "pip install anthropic"
        ) from exc

    client = Anthropic(api_key=api_key)
    system_block: Any = system
    if cache_system:
        system_block = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_block,
        messages=[{"role": "user", "content": user}],
    )

    text = "".join(getattr(block, "text", "") for block in msg.content if hasattr(block, "text")).strip()
    usage = getattr(msg, "usage", None)
    token_usage = {
        "prompt": int(getattr(usage, "input_tokens", 0) or 0),
        "completion": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_creation": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }
    return RouterResult(
        text=text,
        auth="api-key",
        model=model,
        token_usage=token_usage,
        elapsed_ms=0,
    )


def router_status() -> dict[str, Any]:
    """For healthcheck. Returns which Tier(s) are available right now."""
    return {
        "cli_available": _cli_available(),
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "prefer_api_key": _prefer_api_key(),
        "default_model": DEFAULT_MODEL,
    }
