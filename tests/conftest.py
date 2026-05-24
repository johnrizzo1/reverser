"""Shared pytest fixtures for the reverser test suite."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_targets_dir(tmp_path_factory, monkeypatch):
    """Isolate REVERSER_TARGETS_DIR per test so session snapshots, KB
    rows, and other on-disk state never leak into the developer's
    repo-root `targets/` directory.

    Autouse — every test gets a fresh isolated dir by default. The
    explicit `tmp_targets_dir` fixture below is preserved for tests
    that want the path as a return value, and re-uses this env state.

    Regression: tests in tests/test_agent_session_callbacks.py and
    tests/gui_service/test_session_adapter.py historically created
    AgentSession/GUISession instances without requesting tmp_targets_dir,
    leaving orphan snapshots under targets/noop/, targets/bin/, etc.
    Those snapshots then surfaced in the desktop UI sessions list as
    stale "active" rows that couldn't be archived.
    """
    targets_dir = tmp_path_factory.mktemp("reverser-targets")
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    from reverser import paths
    paths._reset_caches_for_tests()
    try:
        import reverser.kb
        reverser.kb._kb_cache.clear()
    except ImportError:
        pass
    yield
    paths._reset_caches_for_tests()


@pytest.fixture
def tmp_targets_dir(tmp_path, monkeypatch):
    """Set REVERSER_TARGETS_DIR to a *named* tmp dir and return the path.

    The autouse fixture above already isolates state for every test;
    request this fixture only when you need the directory path itself
    (e.g., to chdir into it or write a fixture file beneath it).
    """
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    from reverser import paths
    paths._reset_caches_for_tests()
    try:
        import reverser.kb
        reverser.kb._kb_cache.clear()
    except ImportError:
        pass
    return targets_dir


@pytest.fixture
def kb(tmp_targets_dir):
    """Return a fresh KB instance for the test target '10.10.10.5'."""
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb import for_target
    return for_target("10.10.10.5")
