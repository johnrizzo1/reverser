"""Tests for src/reverser/paths.py — storage root resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_path_env(monkeypatch):
    """Ensure env-var overrides and logger state don't leak between tests."""
    for var in ("REVERSER_TARGETS_DIR", "REVERSER_LOGS_DIR", "REVERSER_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    from reverser import paths
    paths._reset_caches_for_tests()
    paths._reset_logger_for_tests()
    yield
    paths._reset_caches_for_tests()
    paths._reset_logger_for_tests()


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


def test_targets_root_uses_env_var_when_set(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(explicit))
    from reverser import paths

    assert paths.targets_root() == explicit


def test_targets_root_uses_project_marker_when_no_env(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.targets_root() == tmp_path.resolve() / "targets"


def test_targets_root_falls_back_to_platformdirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from reverser import paths
    import platformdirs

    expected = Path(platformdirs.user_data_dir("reverser")) / "targets"
    assert paths.targets_root() == expected


def test_logs_root_follows_project_marker(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.logs_root() == tmp_path.resolve() / "logs"


def test_cache_root_does_not_follow_project_marker(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths
    import platformdirs

    expected = Path(platformdirs.user_cache_dir("reverser"))
    assert paths.cache_root() == expected


def test_log_resolved_roots_names_each_source(tmp_path, monkeypatch, caplog):
    import logging
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(explicit))
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)

    from reverser import paths
    with caplog.at_level(logging.INFO, logger="reverser.paths"):
        paths.log_resolved_roots()

    text = caplog.text
    assert "targets_root" in text
    assert "env REVERSER_TARGETS_DIR" in text
    assert "logs_root" in text
    assert "project marker" in text  # logs follow project marker


def test_log_resolved_roots_writes_to_stderr_when_no_logging_configured(tmp_path):
    """Without any logging.basicConfig, log_resolved_roots() must still produce output.

    Runs in a subprocess to avoid pytest's live-logging infrastructure, which
    pre-populates the root logger with null handlers and would mask the defect
    being tested.
    """
    import subprocess
    import sys

    script = (
        "import os, sys;"
        f"os.chdir({str(tmp_path)!r});"
        "from reverser.paths import log_resolved_roots;"
        "log_resolved_roots()"
    )
    src_path = str(Path(__file__).resolve().parent.parent / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": src_path},
    )
    assert result.returncode == 0, result.stderr
    # Output goes to stderr.
    assert "targets_root" in result.stderr
    assert "logs_root" in result.stderr
    assert "cache_root" in result.stderr
