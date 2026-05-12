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


# ── Singleton state + lifecycle ────────────────────────────────────


_state: dict[str, Any] = {
    "playwright": None,        # PlaywrightContextManager instance
    "browser": None,           # Browser
    "context": None,           # BrowserContext
    "page": None,              # Page
    "target": None,            # current target identifier
    "started_at": None,        # ISO timestamp
    "screenshots_taken": 0,
}


def _ensure_browser(target: str, viewport: tuple[int, int] = (1280, 800)):
    """Idempotent lazy-launch. Returns the Page reference.

    Per spec D7: raises RuntimeError if called with a different target while
    a browser is already running for another target. Caller must explicitly
    web_browser_close() before switching.
    """
    if _state["browser"] and _state["browser"].is_connected():
        if _state["target"] != target:
            raise RuntimeError(
                f"Browser is running for target={_state['target']!r}. "
                f"Call web_browser_close() before switching to {target!r}."
            )
        return _state["page"]

    # Lazy import — only loaded when we actually launch a browser
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]}
    )
    page = context.new_page()
    _state.update({
        "playwright": pw,
        "browser": browser,
        "context": context,
        "page": page,
        "target": target,
        "started_at": _now_iso(),
        "screenshots_taken": 0,
    })
    return page


def _close_browser() -> None:
    """Idempotent teardown. Safe to call on empty state."""
    try:
        if _state["browser"] and _state["browser"].is_connected():
            _state["browser"].close()
        if _state["playwright"]:
            _state["playwright"].stop()
    except Exception:
        # Best-effort cleanup; never raise from teardown
        pass
    for k in ("playwright", "browser", "context", "page", "target", "started_at"):
        _state[k] = None
    _state["screenshots_taken"] = 0


# Register the cleanup hook so a process crash doesn't leak the browser
atexit.register(_close_browser)


# ── Tool imports (lazy — only when we have a real @tool to register) ─


from claude_agent_sdk import tool

from ..kb import require_pentest_auth, AuthorizationError
from ._common import format_tool_result, format_error


# ── Tools: lifecycle ───────────────────────────────────────────────


@tool(
    "web_browser_status",
    "Report browser state. Read-only: does not launch a browser, does not "
    "modify state. Returns running/not-running, current URL, target, screenshots taken.",
    {"type": "object", "properties": {}, "required": []},
)
async def web_browser_status(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    running = bool(_state["browser"]) and _state["browser"].is_connected() if _state["browser"] else False

    if not running:
        return format_tool_result(
            "web_browser status:\n"
            "  daemon:  not_running\n"
            "  start with: web_browser_start(target)"
        )

    page = _state["page"]
    current_url = page.url if page else "<unknown>"
    lines = [
        "web_browser status:",
        f"  daemon:            running",
        f"  target:            {_state['target']}",
        f"  current_url:       {current_url}",
        f"  started_at:        {_state['started_at']}",
        f"  screenshots_taken: {_state['screenshots_taken']}",
    ]
    return format_tool_result("\n".join(lines))


TOOLS: list = []
TOOLS.append(web_browser_status)
