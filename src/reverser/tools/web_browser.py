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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from reverser.paths import targets_root


# ── Path helpers ────────────────────────────────────────────────────


def _evidence_dir(target: str, finding_id: int) -> Path:
    """Returns targets/<target>/findings/<finding_id>/, created if absent."""
    p = targets_root() / target / "findings" / str(finding_id)
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
    "network_log": deque(maxlen=200),   # recent requests
    "console_errors": deque(maxlen=50), # recent console errors
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

    # Install network + console listeners. Best-effort; failures don't break launch.
    try:
        page.on("request", lambda req: _state["network_log"].append({
            "method": req.method, "url": req.url,
            "resource_type": req.resource_type,
        }))
        page.on("console", lambda msg:
            _state["console_errors"].append(msg.text) if msg.type == "error" else None
        )
    except Exception:
        pass

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
    _state["network_log"].clear()
    _state["console_errors"].clear()


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


@tool(
    "web_browser_start",
    "Launch headless Chromium and create a context+page bound to the given "
    "target. Idempotent: returns 'already_running' if the singleton browser "
    "is already up. Scope-checked: target's host must be in scope per "
    "scope.toml if one exists. Refuses target switch without explicit "
    "web_browser_close() first.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string",
                       "description": "Target identifier (IP, hostname, or URL host)"},
            "viewport_w": {"type": "integer", "default": 1280,
                           "description": "Viewport width in pixels"},
            "viewport_h": {"type": "integer", "default": 800,
                           "description": "Viewport height in pixels"},
        },
        "required": ["target"],
    },
)
async def web_browser_start(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target = args["target"]
    viewport_w = int(args.get("viewport_w", 1280))
    viewport_h = int(args.get("viewport_h", 800))

    # Scope check BEFORE launching browser. We synthesize an http URL from the
    # target so the same parser-based helper works.
    from ..kb.scope import ScopeError
    try:
        _assert_url_in_scope(f"http://{target}", target)
    except ScopeError as e:
        return format_error(f"scope.toml violation: {e}")

    already = bool(_state["browser"]) and \
              (_state["browser"].is_connected() if _state["browser"] else False) and \
              _state["target"] == target

    try:
        # Bridge sync Playwright to async via asyncio.to_thread (per D6)
        await asyncio.to_thread(
            _ensure_browser, target, (viewport_w, viewport_h)
        )
    except RuntimeError as e:
        return format_error(str(e))
    except Exception as e:
        return format_error(
            f"Failed to launch browser: {type(e).__name__}: {e}. "
            f"Is Chromium installed? Run: playwright install chromium"
        )

    status = "already_running" if already else "started"
    lines = [
        f"web_browser {status}.",
        f"  target:     {_state['target']}",
        f"  viewport:   {viewport_w}x{viewport_h}",
        f"  started_at: {_state['started_at']}",
    ]
    return format_tool_result("\n".join(lines))


TOOLS.append(web_browser_start)


@tool(
    "web_browser_close",
    "Clean shutdown — close page, context, browser; stop playwright; clear "
    "singleton state. Idempotent. Call this before switching to a different "
    "target.",
    {"type": "object", "properties": {}, "required": []},
)
async def web_browser_close(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    was_running = bool(_state["browser"])
    await asyncio.to_thread(_close_browser)

    if was_running:
        return format_tool_result("web_browser closed.\n  status: closed")
    return format_tool_result(
        "web_browser was not running.\n  status: not_running"
    )


TOOLS.append(web_browser_close)


def _require_running() -> dict | None:
    """Returns an error dict if no browser is running. None if running.

    Used by navigation/interaction/observation tools that need the singleton
    populated. (web_browser_start handles its own lazy launch; everything
    else just checks.)
    """
    if not _state["browser"] or not _state["browser"].is_connected():
        return format_error(
            "Browser is not running. Call web_browser_start(target) first."
        )
    return None


@tool(
    "web_browser_navigate",
    "Navigate the current page to a URL. Scope-checked: URL's host must be "
    "in_scope per scope.toml if present. wait_until: 'load' (default) | "
    "'domcontentloaded' | 'networkidle'. Returns final URL (post-redirects), "
    "HTTP status, and page title.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Target URL"},
            "wait_until": {"type": "string", "default": "load",
                           "enum": ["load", "domcontentloaded", "networkidle"]},
            "timeout_ms": {"type": "integer", "default": 30000},
        },
        "required": ["url"],
    },
)
async def web_browser_navigate(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_running()
    if err:
        return err

    url = args["url"]
    wait_until = args.get("wait_until", "load")
    timeout_ms = int(args.get("timeout_ms", 30000))
    target = _state["target"]

    from ..kb.scope import ScopeError
    try:
        _assert_url_in_scope(url, target)
    except ScopeError as e:
        return format_error(f"scope.toml violation: {e}")

    page = _state["page"]

    def _do():
        resp = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {
            "url_requested": url,
            "url_final": page.url,
            "status": resp.status if resp else None,
            "title": page.title(),
        }

    try:
        result = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(
            f"Navigation failed: {type(e).__name__}: {e}"
        )

    return format_tool_result(
        f"Navigated.\n"
        f"  url_requested: {result['url_requested']}\n"
        f"  url_final:     {result['url_final']}\n"
        f"  status:        {result['status']}\n"
        f"  title:         {result['title']}"
    )


TOOLS.append(web_browser_navigate)


@tool(
    "web_browser_click",
    "Click an element on the current page. Selector is CSS or Playwright's "
    "text=/role= form (e.g. 'text=Login', 'role=button[name=\"Submit\"]').",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "timeout_ms": {"type": "integer", "default": 5000},
        },
        "required": ["selector"],
    },
)
async def web_browser_click(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    selector = args["selector"]
    timeout_ms = int(args.get("timeout_ms", 5000))
    page = _state["page"]

    def _do():
        page.click(selector, timeout=timeout_ms)
        return page.url

    try:
        post_url = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"Click failed on {selector!r}: {type(e).__name__}: {e}")

    return format_tool_result(
        f"Clicked.\n  selector:       {selector}\n  post_click_url: {post_url}"
    )


@tool(
    "web_browser_type",
    "Type text into an input element. clear_first=True (default) uses "
    "Playwright's fill() which replaces existing value; clear_first=False "
    "uses type() which appends.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "clear_first": {"type": "boolean", "default": True},
            "timeout_ms": {"type": "integer", "default": 5000},
        },
        "required": ["selector", "text"],
    },
)
async def web_browser_type(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    selector = args["selector"]
    text = args["text"]
    clear_first = bool(args.get("clear_first", True))
    timeout_ms = int(args.get("timeout_ms", 5000))
    page = _state["page"]

    def _do():
        if clear_first:
            page.fill(selector, text, timeout=timeout_ms)
        else:
            page.type(selector, text, timeout=timeout_ms)

    try:
        await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"Type failed on {selector!r}: {type(e).__name__}: {e}")

    return format_tool_result(
        f"Typed.\n  selector:    {selector}\n  text_length: {len(text)}"
    )


TOOLS.extend([web_browser_click, web_browser_type])


@tool(
    "web_browser_fill_form",
    "Fill multiple form fields in one call, optionally submitting. fields "
    "is a {selector: value} dict. submit_selector triggers a click after "
    "filling. Convenience wrapper for the common login-form pattern.",
    {
        "type": "object",
        "properties": {
            "fields": {"type": "object",
                       "description": "{selector: value} dict"},
            "submit_selector": {"type": "string", "default": ""},
            "submit_wait_until": {"type": "string", "default": "load"},
        },
        "required": ["fields"],
    },
)
async def web_browser_fill_form(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    fields: dict = args.get("fields") or {}
    submit_selector = (args.get("submit_selector") or "").strip()
    page = _state["page"]

    def _do():
        count = 0
        for sel, val in fields.items():
            page.fill(sel, str(val))
            count += 1
        submitted = False
        post_url = page.url
        if submit_selector:
            page.click(submit_selector)
            submitted = True
            post_url = page.url
        return count, submitted, post_url

    try:
        count, submitted, post_url = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"fill_form failed: {type(e).__name__}: {e}")

    return format_tool_result(
        f"Form filled.\n"
        f"  fields_filled:   {count}\n"
        f"  submitted:       {submitted}\n"
        f"  post_submit_url: {post_url}"
    )


@tool(
    "web_browser_evaluate",
    "Run arbitrary JavaScript in the page context. Returns the serialized "
    "result. The power tool for 'did this payload actually fire?' checks "
    "and SPA-state inspection.",
    {
        "type": "object",
        "properties": {
            "js": {"type": "string", "description": "JS expression or function body"},
        },
        "required": ["js"],
    },
)
async def web_browser_evaluate(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    js = args["js"]
    page = _state["page"]

    def _do():
        return page.evaluate(js)

    try:
        result = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"evaluate failed: {type(e).__name__}: {e}")

    return format_tool_result(
        f"JS evaluated.\n  result: {result!r}"
    )


@tool(
    "web_browser_wait_for",
    "Block until a selector becomes visible OR text appears on the page. "
    "Exactly one of selector/text must be given. Used for async UI waits "
    "(modals, search results, SPA route changes).",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "default": ""},
            "text": {"type": "string", "default": ""},
            "timeout_ms": {"type": "integer", "default": 5000},
        },
        "required": [],
    },
)
async def web_browser_wait_for(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    selector = (args.get("selector") or "").strip()
    text = (args.get("text") or "").strip()
    timeout_ms = int(args.get("timeout_ms", 5000))

    if not selector and not text:
        return format_error("Must provide either selector or text")
    if selector and text:
        return format_error("Provide either selector OR text, not both")

    page = _state["page"]

    def _do():
        if selector:
            page.wait_for_selector(selector, timeout=timeout_ms)
            return f"selector {selector!r} appeared"
        else:
            page.wait_for_function(
                f"() => document.body && document.body.innerText.includes({json.dumps(text)})",
                timeout=timeout_ms,
            )
            return f"text {text!r} appeared"

    try:
        msg = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(
            f"wait_for timed out or failed: {type(e).__name__}: {e}"
        )

    return format_tool_result(f"Wait succeeded.\n  {msg}")


TOOLS.extend([web_browser_fill_form, web_browser_evaluate, web_browser_wait_for])


@tool(
    "web_browser_snapshot",
    "Return a compact accessibility-tree snapshot of the current page — the "
    "agent's 'look at the page' tool. Cheaper than reading full HTML. "
    "Includes interactive elements and recent console errors.",
    {"type": "object", "properties": {}, "required": []},
)
async def web_browser_snapshot(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    page = _state["page"]

    def _do():
        return {
            "url": page.url,
            "title": page.title(),
            "ax_tree": page.accessibility.snapshot(),
            "console_errors": list(_state["console_errors"])[-10:],
        }

    try:
        snap = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"snapshot failed: {type(e).__name__}: {e}")

    # Compactly render the ax-tree as JSON. Truncate to keep token cost low.
    tree_str = json.dumps(snap["ax_tree"], indent=2)
    if len(tree_str) > 6000:
        tree_str = tree_str[:6000] + "\n... [TRUNCATED — ax-tree too large; "\
                                     "use evaluate(`document.querySelectorAll(...)`) instead]"

    err_lines = "\n  ".join(snap["console_errors"]) or "(none)"
    return format_tool_result(
        f"Page snapshot:\n"
        f"  url:    {snap['url']}\n"
        f"  title:  {snap['title']}\n"
        f"\n--- accessibility tree ---\n{tree_str}\n"
        f"\n--- recent console errors ---\n  {err_lines}"
    )


@tool(
    "web_browser_network_log",
    "Return recent HTTP requests/responses seen by the browser. Critical "
    "for API discovery — the SPA called /api/v2/users/me, that's not in "
    "any path-fuzzing wordlist. last_n defaults to 50; filter_url is an "
    "optional substring filter.",
    {
        "type": "object",
        "properties": {
            "last_n": {"type": "integer", "default": 50},
            "filter_url": {"type": "string", "default": ""},
        },
        "required": [],
    },
)
async def web_browser_network_log(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    last_n = int(args.get("last_n", 50))
    filter_url = (args.get("filter_url") or "").strip()

    requests = list(_state["network_log"])
    if filter_url:
        requests = [r for r in requests if filter_url in r["url"]]
    requests = requests[-last_n:]

    if not requests:
        return format_tool_result("No requests logged.")

    lines = [f"Recent {len(requests)} request(s):", ""]
    for r in requests:
        lines.append(f"  {r['method']:6s} {r['url']}  [{r['resource_type']}]")
    return format_tool_result("\n".join(lines))


TOOLS.extend([web_browser_snapshot, web_browser_network_log])


# ── Composed workflow helpers ──────────────────────────────────────


def _capture_to_finding(page, target: str, finding_id: int) -> dict:
    """Screenshot → save → hash → record_artifact → append_finding_evidence.

    Returns: {path, sha256, screenshot_index}
    """
    from ..kb import for_target as _for_target, ArtifactFact as _ArtifactFact

    path = _next_screenshot_path(target, finding_id)
    page.screenshot(path=str(path), full_page=True)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    kb = _for_target(target)
    kb.record_artifact(_ArtifactFact(
        kind="screenshot", path=str(path), sha256=sha,
        source_tool="web_browser",
    ))
    kb.append_finding_evidence(finding_id, str(path))
    _state["screenshots_taken"] += 1
    return {
        "path": str(path),
        "sha256": sha,
        "screenshot_index": _state["screenshots_taken"],
    }


@tool(
    "web_browser_capture_finding",
    "Take a full-page screenshot of the current page, save it under "
    "targets/<target>/findings/<finding_id>/screenshot-<n>.png, record an "
    "ArtifactFact, and append the path to the finding's evidence_paths. "
    "ONE call → deliverable-ready evidence.",
    {
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer",
                           "description": "Existing finding id to attach evidence to"},
            "description": {"type": "string", "default": "",
                            "description": "Optional context note (logged, not stored)"},
        },
        "required": ["finding_id"],
    },
)
async def web_browser_capture_finding(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    finding_id = int(args["finding_id"])
    description = args.get("description", "")
    target = _state["target"]
    page = _state["page"]

    def _do():
        return _capture_to_finding(page, target, finding_id)

    try:
        result = await asyncio.to_thread(_do)
    except ValueError as e:
        # Raised by append_finding_evidence when finding_id doesn't exist
        return format_error(str(e))
    except Exception as e:
        return format_error(f"capture_finding failed: {type(e).__name__}: {e}")

    desc_note = f"  description:      {description}\n" if description else ""
    return format_tool_result(
        f"Screenshot captured.\n"
        f"  finding_id:       {finding_id}\n"
        f"  path:             {result['path']}\n"
        f"  sha256:           {result['sha256']}\n"
        f"  screenshot_index: {result['screenshot_index']}\n"
        f"{desc_note}"
    )


TOOLS.append(web_browser_capture_finding)


@tool(
    "web_browser_confirm_xss",
    "Multi-step XSS confirmation: install sentinel + dialog handler → "
    "inject payload → submit → wait → check whether the payload actually "
    "executed. Returns confirmed/refuted with evidence type. Won't catch "
    "silent-side-effect payloads (e.g. fetch() exfil).",
    {
        "type": "object",
        "properties": {
            "payload": {"type": "string"},
            "navigate_to": {"type": "string", "default": ""},
            "inject_selector": {"type": "string", "default": ""},
            "submit_selector": {"type": "string", "default": ""},
            "sentinel_global": {"type": "string", "default": "__xss_fired_sentinel__"},
            "wait_ms": {"type": "integer", "default": 500},
            "finding_id": {"type": "integer", "default": 0,
                           "description": "If >0, auto-capture screenshot via capture_finding on confirmed"},
        },
        "required": ["payload"],
    },
)
async def web_browser_confirm_xss(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    payload = args["payload"]
    navigate_to = (args.get("navigate_to") or "").strip()
    inject_selector = (args.get("inject_selector") or "").strip()
    submit_selector = (args.get("submit_selector") or "").strip()
    sentinel = args.get("sentinel_global") or "__xss_fired_sentinel__"
    wait_ms = int(args.get("wait_ms", 500))
    finding_id = int(args.get("finding_id", 0))

    page = _state["page"]
    target = _state["target"]

    # Track dialog firings
    dialog_fired = {"hit": False}
    try:
        page.on("dialog", lambda d: (dialog_fired.update({"hit": True}), d.dismiss()))
    except Exception:
        pass

    def _do():
        if navigate_to:
            from ..kb.scope import ScopeError
            try:
                _assert_url_in_scope(navigate_to, target)
            except ScopeError as e:
                return {"error": f"scope.toml violation: {e}"}
            page.goto(navigate_to, wait_until="load")

        # Install sentinel global
        page.evaluate(f"() => {{ window.{sentinel} = false; }}")

        if inject_selector:
            page.fill(inject_selector, payload)
        if submit_selector:
            page.click(submit_selector)

        # Brief wait for payload execution
        page.wait_for_timeout(wait_ms)

        # Check sentinel
        sentinel_fired = bool(page.evaluate(f"window.{sentinel}"))
        return {"sentinel_fired": sentinel_fired}

    try:
        result = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"confirm_xss failed: {type(e).__name__}: {e}")

    if "error" in result:
        return format_error(result["error"])

    sentinel_fired = result["sentinel_fired"]
    recent_errors = list(_state["console_errors"])[-5:]
    eval_error = any(
        "Uncaught" in e or "SyntaxError" in e or "ReferenceError" in e
        for e in recent_errors
    )

    if sentinel_fired:
        evidence = "sentinel_global"
        confirmed = True
    elif dialog_fired["hit"]:
        evidence = "dialog_fired"
        confirmed = True
    elif eval_error:
        evidence = "console_eval"
        confirmed = True
    else:
        evidence = "neither"
        confirmed = False

    screenshot_path = None
    if confirmed and finding_id > 0:
        try:
            cap = _capture_to_finding(page, target, finding_id)
            screenshot_path = cap["path"]
        except Exception:
            pass

    return format_tool_result(
        f"XSS check:\n"
        f"  confirmed:        {confirmed}\n"
        f"  evidence:         {evidence}\n"
        f"  payload:          {payload[:120]}\n"
        f"  screenshot_path:  {screenshot_path or '(none)'}"
    )


TOOLS.append(web_browser_confirm_xss)


@tool(
    "web_browser_crawl",
    "BFS crawl with JS rendering. Follows links, optionally fills forms "
    "with placeholder values to discover routes. Scope-checked per URL "
    "(out-of-scope silently skipped, counted). Hard caps: max_pages, "
    "max_depth, same_origin. Returns discovered URLs/forms/APIs — does "
    "NOT auto-write to KB (agent decides what to persist).",
    {
        "type": "object",
        "properties": {
            "start_url": {"type": "string"},
            "max_pages": {"type": "integer", "default": 30},
            "max_depth": {"type": "integer", "default": 2},
            "same_origin": {"type": "boolean", "default": True},
            "fill_forms": {"type": "boolean", "default": False},
        },
        "required": ["start_url"],
    },
)
async def web_browser_crawl(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))
    err = _require_running()
    if err:
        return err

    start_url = args["start_url"]
    max_pages = int(args.get("max_pages", 30))
    max_depth = int(args.get("max_depth", 2))
    same_origin = bool(args.get("same_origin", True))
    fill_forms = bool(args.get("fill_forms", False))

    target = _state["target"]
    page = _state["page"]

    from ..kb.scope import ScopeError

    start_origin = urlparse(start_url).netloc

    def _do():
        visited: list[str] = []
        forms: list[dict] = []
        queue: list[tuple[str, int]] = [(start_url, 0)]  # (url, depth)
        seen: set[str] = set()
        out_of_scope = 0
        api_set: set[str] = set()
        # Snapshot network log before crawl so we can diff to find APIs
        baseline_net = len(_state["network_log"])

        while queue and len(visited) < max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)

            try:
                _assert_url_in_scope(url, target)
            except ScopeError:
                out_of_scope += 1
                continue

            if same_origin and urlparse(url).netloc != start_origin:
                continue

            try:
                page.goto(url, wait_until="load", timeout=15000)
                visited.append(url)
            except Exception:
                continue

            # Find forms (best-effort)
            try:
                form_els = page.query_selector_all("form")
                for fe in form_els:
                    forms.append({
                        "action": fe.get_attribute("action") or url,
                        "method": (fe.get_attribute("method") or "GET").upper(),
                    })
                    if fill_forms:
                        # Fill inputs with placeholder values to potentially
                        # discover server-side routes
                        try:
                            inputs = fe.query_selector_all("input")
                            for inp in inputs:
                                t = inp.get_attribute("type") or "text"
                                if t in ("text", "email", "search"):
                                    inp.fill("test")
                        except Exception:
                            pass
            except Exception:
                pass

            if depth < max_depth:
                try:
                    links = page.evaluate(
                        "() => Array.from(document.querySelectorAll('a[href]'))"
                        ".map(a => a.href).filter(u => u.startsWith('http'))"
                    ) or []
                    for link in links:
                        if link not in seen:
                            queue.append((link, depth + 1))
                except Exception:
                    pass

        # Diff network log to extract APIs called during the crawl
        new_requests = list(_state["network_log"])[baseline_net:]
        for r in new_requests:
            if r["resource_type"] in ("xhr", "fetch"):
                api_set.add(r["url"])

        return {
            "pages_visited": visited,
            "forms_discovered": forms,
            "apis_called": sorted(api_set),
            "out_of_scope_skipped": out_of_scope,
            "capped": len(visited) >= max_pages,
        }

    try:
        result = await asyncio.to_thread(_do)
    except Exception as e:
        return format_error(f"crawl failed: {type(e).__name__}: {e}")

    lines = [
        f"Crawl complete:",
        f"  pages_visited:        {len(result['pages_visited'])}",
        f"  forms_discovered:     {len(result['forms_discovered'])}",
        f"  apis_called:          {len(result['apis_called'])}",
        f"  out_of_scope_skipped: {result['out_of_scope_skipped']}",
        f"  capped:               {result['capped']}",
        "",
        "Visited URLs:",
    ]
    for u in result["pages_visited"][:30]:
        lines.append(f"  {u}")
    if result["apis_called"]:
        lines.append("\nAPI endpoints observed:")
        for u in result["apis_called"][:20]:
            lines.append(f"  {u}")
    return format_tool_result("\n".join(lines))


TOOLS.append(web_browser_crawl)
