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
