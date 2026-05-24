"""Tests for src/reverser/paths.py — storage root resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_path_env(monkeypatch):
    """Ensure env-var overrides don't leak between tests."""
    for var in ("REVERSER_TARGETS_DIR", "REVERSER_LOGS_DIR", "REVERSER_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    # Force a fresh paths module each test so its lru_cache resets.
    import importlib
    import reverser.paths as paths_mod
    importlib.reload(paths_mod)
    yield


def test_project_root_returns_none_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.project_root() is None


def test_project_root_finds_marker_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.project_root() == tmp_path.resolve()


def test_project_root_finds_marker_in_ancestor(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    from reverser import paths

    assert paths.project_root() == tmp_path.resolve()


def test_project_root_refuses_home_directory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".reverser-authorized").touch()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(fake_home)
    from reverser import paths

    assert paths.project_root() is None
