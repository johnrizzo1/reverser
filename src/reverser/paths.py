"""Resolves persistent storage paths for reverser.

Three-layer precedence for every root:
  1. Explicit env var (REVERSER_*_DIR) — highest
  2. Project marker (.reverser-authorized) in CWD or ancestor
  3. Platform-native default via platformdirs — lowest
"""
from __future__ import annotations

import functools
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
