"""Tests for the optional per-target scope.toml loader and enforcement."""

import pytest

from reverser.kb.scope import (
    Scope,
    ScopeError,
    load_scope,
)


def test_load_scope_missing_file_returns_none(tmp_targets_dir):
    assert load_scope("10.10.10.5") is None


def test_load_scope_basic(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        'out_of_scope_ips = ["10.10.10.5"]\n'
        'allowed_hours = "08:00-18:00 America/New_York"\n'
        "no_dos = true\n"
        "no_account_lockout = true\n"
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.in_scope_cidrs == ["10.10.10.0/24"]
    assert scope.out_of_scope_ips == ["10.10.10.5"]
    assert scope.no_dos is True
    assert scope.no_account_lockout is True


def test_load_scope_minimal(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.in_scope_cidrs == ["10.10.10.0/24"]
    assert scope.out_of_scope_ips == []
    assert scope.no_dos is False
    assert scope.no_account_lockout is False


def test_load_scope_invalid_toml_raises(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text("this is not = valid toml [\n")
    with pytest.raises(ScopeError) as exc_info:
        load_scope("10.10.10.5")
    assert "scope.toml" in str(exc_info.value)


def test_is_target_in_scope_inside_cidr(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24", "192.168.1.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.is_target_in_scope("10.10.10.42") is True
    assert scope.is_target_in_scope("192.168.1.10") is True
    assert scope.is_target_in_scope("172.16.0.1") is False


def test_is_target_in_scope_excluded_ip(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        'out_of_scope_ips = ["10.10.10.42"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.is_target_in_scope("10.10.10.41") is True
    assert scope.is_target_in_scope("10.10.10.42") is False


def test_is_target_in_scope_empty_cidr_list_allows_all(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'out_of_scope_ips = ["1.2.3.4"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.is_target_in_scope("172.16.0.1") is True
    assert scope.is_target_in_scope("1.2.3.4") is False


def test_assert_in_scope_passes(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    scope.assert_in_scope("10.10.10.42")


def test_assert_in_scope_raises_for_out_of_scope(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_in_scope("172.16.0.1")
    assert "out of scope" in str(exc_info.value).lower()
    assert "172.16.0.1" in str(exc_info.value)


def test_assert_spray_allowed_default(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    scope.assert_spray_allowed()


def test_assert_spray_allowed_blocked(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        "no_account_lockout = true\n"
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_spray_allowed()
    assert "lockout" in str(exc_info.value).lower()


def test_assert_dos_allowed_blocked(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        "no_dos = true\n"
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_dos_allowed()
    assert "dos" in str(exc_info.value).lower() or "denial" in str(exc_info.value).lower()


def test_load_scope_normalizes_target_id(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("  10.10.10.5  ")
    assert scope is not None


def test_scope_re_exported_from_package():
    """Public API: `from reverser.kb import load_scope, Scope, ScopeError`."""
    from reverser.kb import load_scope, Scope, ScopeError
    assert callable(load_scope)
    assert isinstance(Scope(), Scope)
    assert issubclass(ScopeError, RuntimeError)


def test_netexec_smb_respects_scope(tmp_targets_dir, monkeypatch):
    """If scope.toml excludes a target, netexec_smb returns a scope error and never shells out."""
    import asyncio
    target_dir = tmp_targets_dir / "172.16.0.1"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")

    from reverser.tools import netexec as netexec_mod
    called = []
    monkeypatch.setattr(
        netexec_mod, "run_cmd",
        lambda *a, **kw: called.append((a, kw)) or {"stdout": "", "stderr": "", "returncode": 0, "truncated": False},
        raising=False,
    )

    fn = getattr(netexec_mod.netexec_smb, "handler", None) or netexec_mod.netexec_smb
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(fn({
            "target": "172.16.0.1", "action": "check_auth",
            "username": "x", "password": "y",
        }))
    finally:
        loop.close()

    assert result.get("is_error") is True
    assert "scope.toml violation" in result["content"][0]["text"]
    assert called == []  # subprocess was never invoked
