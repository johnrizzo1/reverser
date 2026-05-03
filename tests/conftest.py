"""Shared pytest fixtures for the reverser test suite."""

import pytest


@pytest.fixture
def tmp_targets_dir(tmp_path, monkeypatch):
    """Set REVERSER_TARGETS_DIR to a tmp dir for the duration of the test."""
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    return targets_dir


@pytest.fixture
def kb(tmp_targets_dir):
    """Return a fresh KB instance for the test target '10.10.10.5'."""
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb import for_target
    return for_target("10.10.10.5")
