"""Tests for web_browser module — Playwright integration for webpentest profile."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Group 1: Pure helpers (no Playwright dependency) ─────────────────


def test_targets_root_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.tools.web_browser import _targets_root
    assert _targets_root() == tmp_path


def test_evidence_dir_creates_findings_subdir(tmp_targets_dir):
    from reverser.tools.web_browser import _evidence_dir
    p = _evidence_dir("10.10.10.5", 42)
    assert p == tmp_targets_dir / "10.10.10.5" / "findings" / "42"
    assert p.is_dir()


def test_next_screenshot_path_auto_increments(tmp_targets_dir):
    from reverser.tools.web_browser import _next_screenshot_path
    p1 = _next_screenshot_path("10.10.10.5", 7)
    assert p1.name == "screenshot-1.png"
    # Create the file so the next call increments
    p1.write_bytes(b"fake png")
    p2 = _next_screenshot_path("10.10.10.5", 7)
    assert p2.name == "screenshot-2.png"


def test_assert_url_in_scope_passes_when_no_scope_toml(tmp_targets_dir):
    """When no scope.toml exists, all URLs are allowed (existing convention)."""
    from reverser.tools.web_browser import _assert_url_in_scope
    # Should not raise
    _assert_url_in_scope("https://example.com/path", "10.10.10.5")


def test_assert_url_in_scope_blocks_out_of_scope_host(tmp_targets_dir):
    """When scope.toml restricts hosts, out-of-scope URLs raise ScopeError."""
    from reverser.tools.web_browser import _assert_url_in_scope
    from reverser.kb.scope import ScopeError

    # Create scope.toml that only allows 192.168.0.0/24
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    with pytest.raises(ScopeError, match="out of scope"):
        _assert_url_in_scope("https://10.10.10.5/admin", "10.10.10.5")


def test_assert_url_in_scope_allows_relative_url(tmp_targets_dir):
    """Relative URLs / data: URIs have no host — not blocked."""
    from reverser.tools.web_browser import _assert_url_in_scope

    # Create scope.toml that restricts hosts
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    # Should not raise
    _assert_url_in_scope("/api/users", "10.10.10.5")
    _assert_url_in_scope("data:text/html,<h1>test</h1>", "10.10.10.5")


def test_state_dict_initial_values(tmp_targets_dir):
    """Singleton state dict starts with all None / 0."""
    from reverser.tools import web_browser as wb
    # Reset state (in case prior test left it dirty)
    wb._close_browser()
    assert wb._state["browser"] is None
    assert wb._state["page"] is None
    assert wb._state["target"] is None
    assert wb._state["screenshots_taken"] == 0


def test_ensure_browser_launches_when_state_empty(tmp_targets_dir):
    """_ensure_browser launches Chromium when no singleton exists."""
    import sys
    from reverser.tools import web_browser as wb
    wb._close_browser()  # reset

    fake_page = MagicMock()
    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    fake_browser.new_context.return_value = fake_context
    fake_pw_chromium = MagicMock()
    fake_pw_chromium.launch.return_value = fake_browser
    fake_pw = MagicMock()
    fake_pw.chromium = fake_pw_chromium
    fake_sync = MagicMock()
    fake_sync.start.return_value = fake_pw

    fake_sync_api_mod = MagicMock()
    fake_sync_api_mod.sync_playwright = MagicMock(return_value=fake_sync)
    fake_playwright_mod = MagicMock()

    prev_playwright = sys.modules.get("playwright")
    prev_sync_api = sys.modules.get("playwright.sync_api")
    sys.modules["playwright"] = fake_playwright_mod
    sys.modules["playwright.sync_api"] = fake_sync_api_mod
    try:
        page = wb._ensure_browser("10.10.10.5", viewport=(1280, 800))
    finally:
        if prev_playwright is None:
            sys.modules.pop("playwright", None)
        else:
            sys.modules["playwright"] = prev_playwright
        if prev_sync_api is None:
            sys.modules.pop("playwright.sync_api", None)
        else:
            sys.modules["playwright.sync_api"] = prev_sync_api

    assert page is fake_page
    assert wb._state["browser"] is fake_browser
    assert wb._state["target"] == "10.10.10.5"
    wb._close_browser()


def test_ensure_browser_idempotent_for_same_target(tmp_targets_dir):
    """Second call for the same target returns existing page without re-launch."""
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state["browser"] = fake_browser
    wb._state["page"] = fake_page
    wb._state["target"] = "10.10.10.5"

    # No mock for sync_playwright — if it gets called, the test will error
    page = wb._ensure_browser("10.10.10.5")
    assert page is fake_page
    wb._close_browser()


def test_ensure_browser_refuses_target_switch(tmp_targets_dir):
    """Calling with a different target while browser is running raises (D7)."""
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state["browser"] = fake_browser
    wb._state["page"] = MagicMock()
    wb._state["target"] = "10.10.10.5"

    with pytest.raises(RuntimeError, match="web_browser_close"):
        wb._ensure_browser("10.10.10.6")
    wb._close_browser()


def test_close_browser_is_idempotent(tmp_targets_dir):
    """_close_browser on empty state is a no-op (no error)."""
    from reverser.tools import web_browser as wb
    wb._close_browser()
    # Call again — should not raise
    wb._close_browser()
    assert wb._state["browser"] is None


import asyncio


def _call(tool_obj, args):
    """Invoke a @tool-decorated SdkMcpTool object synchronously for testing."""
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def test_status_when_browser_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()  # ensure clean state

    result = _call(wb.web_browser_status, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "not_running" in text.lower()


def test_status_when_browser_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_page = MagicMock()
    fake_page.url = "https://example.com/page"
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser,
        "page": fake_page,
        "target": "10.10.10.5",
        "started_at": "2026-05-12T10:00:00",
        "screenshots_taken": 2,
    })

    result = _call(wb.web_browser_status, {})
    text = result["content"][0]["text"]
    assert "running" in text.lower()
    assert "10.10.10.5" in text
    assert "https://example.com/page" in text
    assert "2" in text  # screenshots_taken
    wb._close_browser()


def test_status_requires_pentest_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools import web_browser as wb
    result = _call(wb.web_browser_status, {})
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()


def test_start_launches_browser_and_returns_status(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    with patch("reverser.tools.web_browser._ensure_browser") as mock_ensure:
        # Make _ensure_browser populate state as a side effect (real impl does this)
        def side_effect(target, viewport=(1280, 800)):
            wb._state.update({
                "browser": fake_browser, "page": fake_page,
                "target": target, "started_at": "2026-05-12T10:00:00",
            })
            return fake_page
        mock_ensure.side_effect = side_effect

        result = _call(wb.web_browser_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "started" in text.lower() or "running" in text.lower()
    assert "10.10.10.5" in text
    mock_ensure.assert_called_once()
    wb._close_browser()


def test_start_refuses_out_of_scope_target(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    # Set up scope.toml that excludes 10.10.10.5
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    # _ensure_browser must NOT be called because scope check fails first
    with patch("reverser.tools.web_browser._ensure_browser") as mock_ensure:
        result = _call(wb.web_browser_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is True
    assert "scope" in result["content"][0]["text"].lower()
    mock_ensure.assert_not_called()


def test_start_idempotent_when_already_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    # Pre-populate state to simulate already-running browser
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": MagicMock(),
        "target": "10.10.10.5", "started_at": "2026-05-12T09:00:00",
    })

    result = _call(wb.web_browser_start, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "already" in text.lower() or "running" in text.lower()
    wb._close_browser()


def test_close_when_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    result = _call(wb.web_browser_close, {})
    text = result["content"][0]["text"]
    assert "not_running" in text.lower() or "not running" in text.lower()


def test_close_when_running_tears_down(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    fake_pw = MagicMock()
    wb._state.update({
        "browser": fake_browser, "playwright": fake_pw,
        "page": MagicMock(), "target": "10.10.10.5",
    })

    result = _call(wb.web_browser_close, {})
    text = result["content"][0]["text"]
    assert "closed" in text.lower()
    assert wb._state["browser"] is None
    fake_browser.close.assert_called_once()
    fake_pw.stop.assert_called_once()


def test_navigate_calls_page_goto_and_returns_status(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_page = MagicMock()
    fake_page.goto.return_value = fake_resp
    fake_page.title.return_value = "Example Page"
    fake_page.url = "https://example.com/landing"
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_navigate, {"url": "https://example.com/landing"})
    text = result["content"][0]["text"]
    assert "200" in text
    assert "example.com" in text
    assert "Example Page" in text
    fake_page.goto.assert_called_once()
    wb._close_browser()


def test_navigate_refuses_out_of_scope_url(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "10.10.10.5",
    })

    result = _call(wb.web_browser_navigate, {"url": "https://10.10.10.5/admin"})
    assert result.get("is_error") is True
    assert "scope" in result["content"][0]["text"].lower()
    fake_page.goto.assert_not_called()
    wb._close_browser()


def test_navigate_without_browser_returns_error(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    result = _call(wb.web_browser_navigate, {"url": "https://example.com"})
    assert result.get("is_error") is True
    assert "start" in result["content"][0]["text"].lower()


def test_click_calls_page_click(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_page.url = "https://example.com/clicked"
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_click, {"selector": "button.submit"})
    assert result.get("is_error") is not True
    fake_page.click.assert_called_once_with("button.submit", timeout=5000)
    wb._close_browser()


def test_type_clears_then_types(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_type, {
        "selector": "input[name='username']", "text": "admin",
    })
    assert result.get("is_error") is not True
    fake_page.fill.assert_called()  # clear_first=True default uses fill (which replaces)
    wb._close_browser()


def test_fill_form_fills_multiple_fields(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_page.url = "https://example.com/dashboard"
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_fill_form, {
        "fields": {"input[name=u]": "admin", "input[name=p]": "secret"},
        "submit_selector": "button[type=submit]",
    })
    assert result.get("is_error") is not True
    # fill called for each field, click for submit
    assert fake_page.fill.call_count == 2
    fake_page.click.assert_called_with("button[type=submit]")
    wb._close_browser()


def test_evaluate_runs_js_and_returns_result(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_page.evaluate.return_value = {"answer": 42}
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_evaluate, {"js": "({answer: 42})"})
    text = result["content"][0]["text"]
    assert "42" in text
    fake_page.evaluate.assert_called_once_with("({answer: 42})")
    wb._close_browser()


def test_wait_for_selector(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_wait_for, {"selector": ".result-row"})
    assert result.get("is_error") is not True
    fake_page.wait_for_selector.assert_called_once()
    wb._close_browser()


def test_snapshot_returns_page_structure(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_page = MagicMock()
    fake_page.url = "https://example.com/dashboard"
    fake_page.title.return_value = "Dashboard"
    fake_page.accessibility.snapshot.return_value = {
        "role": "WebArea", "name": "Dashboard",
        "children": [{"role": "button", "name": "Logout"}],
    }
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_snapshot, {})
    text = result["content"][0]["text"]
    assert "Dashboard" in text
    assert "Logout" in text or "button" in text
    wb._close_browser()


def test_network_log_returns_recent_requests(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": MagicMock(), "target": "example.com",
    })
    wb._state["network_log"].append({
        "method": "GET", "url": "https://example.com/api/users", "resource_type": "xhr",
    })
    wb._state["network_log"].append({
        "method": "POST", "url": "https://example.com/api/login", "resource_type": "xhr",
    })

    result = _call(wb.web_browser_network_log, {})
    text = result["content"][0]["text"]
    assert "/api/users" in text
    assert "/api/login" in text
    wb._close_browser()


def test_network_log_filter_url(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": MagicMock(), "target": "example.com",
    })
    wb._state["network_log"].extend([
        {"method": "GET", "url": "https://example.com/api/users", "resource_type": "xhr"},
        {"method": "GET", "url": "https://example.com/static/app.js", "resource_type": "script"},
        {"method": "POST", "url": "https://example.com/api/login", "resource_type": "xhr"},
    ])

    result = _call(wb.web_browser_network_log, {"filter_url": "/api/"})
    text = result["content"][0]["text"]
    assert "/api/users" in text
    assert "/api/login" in text
    assert "/static/app.js" not in text
    wb._close_browser()


def test_capture_finding_writes_screenshot_and_artifact(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    from reverser.kb import for_target, FindingFact
    wb._close_browser()

    # First create a real finding for the artifact append to land on
    kb = for_target("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Test finding", severity="high", description="",
    ))

    fake_page = MagicMock()
    def fake_screenshot(path, full_page):
        Path(path).write_bytes(b"\x89PNGfake-screenshot-bytes")
    fake_page.screenshot.side_effect = fake_screenshot

    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "10.10.10.5",
    })

    result = _call(wb.web_browser_capture_finding, {
        "finding_id": fid, "description": "Confirmed XSS in /search",
    })

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "screenshot-1.png" in text
    assert "sha256" in text.lower()

    # Artifact was recorded
    artifacts = kb.get_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0].kind == "screenshot"
    assert artifacts[0].source_tool == "web_browser"

    # Finding's evidence_paths updated
    findings = kb.get_findings()
    assert len(findings) == 1
    assert any("screenshot-1.png" in p for p in findings[0].evidence_paths)
    wb._close_browser()


def test_confirm_xss_returns_confirmed_when_sentinel_fires(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    wb._close_browser()

    fake_page = MagicMock()
    fake_page.url = "https://example.com/search"
    # Simulate the sentinel being set after payload execution
    sentinel_state = {"fired": False}
    def fake_evaluate(js):
        if "window." in js and "true" in js:  # initial sentinel install
            return None
        if "window.__xss_fired_sentinel__" in js and "()" not in js:
            # Reading the sentinel — simulate XSS payload fired
            sentinel_state["fired"] = True
            return True
        return None
    fake_page.evaluate.side_effect = fake_evaluate
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })

    result = _call(wb.web_browser_confirm_xss, {
        "payload": "<img src=x onerror='window.__xss_fired_sentinel__=true'>",
    })

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "confirmed" in text.lower()
    wb._close_browser()


def test_confirm_xss_returns_refuted_when_nothing_fires(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools import web_browser as wb
    from collections import deque
    wb._close_browser()

    fake_page = MagicMock()
    fake_page.url = "https://example.com/search"
    fake_page.evaluate.return_value = False  # sentinel stays false
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    wb._state.update({
        "browser": fake_browser, "page": fake_page, "target": "example.com",
    })
    # Ensure console_errors is a clean deque for this test
    wb._state["console_errors"] = deque(maxlen=50)

    result = _call(wb.web_browser_confirm_xss, {
        "payload": "<benign>not actually XSS</benign>",
    })
    text = result["content"][0]["text"]
    assert "neither" in text.lower() or "refuted" in text.lower() or "not confirmed" in text.lower() or "false" in text.lower()
    wb._close_browser()
