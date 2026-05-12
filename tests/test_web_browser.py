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
