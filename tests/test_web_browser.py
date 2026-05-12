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
