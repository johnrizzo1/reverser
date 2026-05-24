"""Resolves persistent storage paths for reverser.

Three-layer precedence for every root:
  1. Explicit env var (REVERSER_*_DIR) — highest
  2. Project marker (.reverser-authorized) in CWD or ancestor
  3. Platform-native default via platformdirs — lowest
"""
from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import Optional

import platformdirs

_APP_NAME = "reverser"
_PROJECT_MARKER = ".reverser-authorized"


@functools.lru_cache(maxsize=1)
def project_root() -> Optional[Path]:
    """Walk up from CWD looking for the project marker file.

    Returns the directory containing .reverser-authorized, or None if
    no marker is found before reaching the filesystem root or $HOME.
    """
    start = Path.cwd().resolve()
    home = Path.home().resolve()
    current = start
    while True:
        marker = current / _PROJECT_MARKER
        if marker.is_file():
            # Refuse $HOME and filesystem root as project roots — too easy
            # to misconfigure.
            if current == home or current == current.parent:
                return None
            return current
        if current == current.parent:  # reached filesystem root
            return None
        current = current.parent


@functools.lru_cache(maxsize=1)
def targets_root() -> Path:
    """Resolve the directory holding per-target data (KB, sessions, scope)."""
    env = os.environ.get("REVERSER_TARGETS_DIR")
    if env:
        return Path(env)
    project = project_root()
    if project is not None:
        return project / "targets"
    return Path(platformdirs.user_data_dir(_APP_NAME)) / "targets"


@functools.lru_cache(maxsize=1)
def logs_root() -> Path:
    """Resolve the directory holding session JSONL logs."""
    env = os.environ.get("REVERSER_LOGS_DIR")
    if env:
        return Path(env)
    project = project_root()
    if project is not None:
        return project / "logs"
    return Path(platformdirs.user_log_dir(_APP_NAME))


@functools.lru_cache(maxsize=1)
def cache_root() -> Path:
    """Resolve the directory for shared caches (wordlists, etc.).

    Caches do NOT follow the project marker — they are shared across
    engagements and should not be duplicated per-project.
    """
    env = os.environ.get("REVERSER_CACHE_DIR")
    if env:
        return Path(env)
    return Path(platformdirs.user_cache_dir(_APP_NAME))


def _reset_caches_for_tests() -> None:
    """Test-only helper: clear lru_caches so monkeypatch'd env/CWD take effect."""
    project_root.cache_clear()
    targets_root.cache_clear()
    logs_root.cache_clear()
    cache_root.cache_clear()


_log = logging.getLogger(__name__)


def _source_label(env_var: str, follows_marker: bool) -> str:
    if os.environ.get(env_var):
        return f"env {env_var}"
    if follows_marker and project_root() is not None:
        return "project marker"
    return "platform default"


def log_resolved_roots() -> None:
    """Emit one INFO line per resolved root naming the precedence layer used."""
    _log.info("targets_root=%s (source: %s)", targets_root(), _source_label("REVERSER_TARGETS_DIR", True))
    _log.info("logs_root=%s (source: %s)", logs_root(), _source_label("REVERSER_LOGS_DIR", True))
    _log.info("cache_root=%s (source: %s)", cache_root(), _source_label("REVERSER_CACHE_DIR", False))
