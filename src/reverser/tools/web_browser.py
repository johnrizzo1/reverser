"""Playwright-based web browser tools for the webpentest/webapi/webrecon profiles.

Wraps the Python playwright library directly (NOT the external Playwright MCP
via npx) so we can:
  - Enforce scope.toml on every navigation
  - Auto-capture screenshots into targets/<target>/findings/<id>/
  - Record ArtifactFacts in the per-target KB

Singleton browser per agent session (per spec D3). Sync Playwright API wrapped
in asyncio.to_thread (per spec D6 — same pattern as bloodhound's sync neo4j
driver). atexit hook cleans up on process exit.

See docs/superpowers/specs/2026-05-12-playwright-webpentest-design.md
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ── Path helpers ────────────────────────────────────────────────────


def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def _evidence_dir(target: str, finding_id: int) -> Path:
    """Returns targets/<target>/findings/<finding_id>/, created if absent."""
    p = _targets_root() / target / "findings" / str(finding_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _next_screenshot_path(target: str, finding_id: int) -> Path:
    """Auto-increment screenshot path: screenshot-1.png, screenshot-2.png, ..."""
    d = _evidence_dir(target, finding_id)
    existing = sorted(d.glob("screenshot-*.png"))
    n = len(existing) + 1
    return d / f"screenshot-{n}.png"


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Scope enforcement ──────────────────────────────────────────────


def _assert_url_in_scope(url: str, target: str) -> None:
    """Parse URL, extract host, route through scope.toml. Raises ScopeError on violation.

    Per spec D8 + §6.3. Used by web_browser_navigate and web_browser_crawl
    (per discovered URL). Relative URLs / data: URIs have no host and are
    not blocked (Playwright resolves them against the current origin).

    If no scope.toml exists for the target, no enforcement happens
    (existing convention from kb/scope.py).
    """
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is None:
        return
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return
    try:
        scope.assert_in_scope(host)
    except ScopeError as e:
        raise ScopeError(
            f"web_browser refusing to navigate to {url!r} — "
            f"host {host!r} is out of scope. ({e})"
        )


# Tools list — populated as @tool handlers are added in subsequent tasks
TOOLS: list = []
