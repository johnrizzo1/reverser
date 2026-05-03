"""Tests for the pentest authorization helper."""

import pytest

from reverser.kb.authz import require_pentest_auth, AuthorizationError


def test_env_var_grants_auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    require_pentest_auth()  # should not raise


def test_authorized_file_grants_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".reverser-authorized").touch()
    require_pentest_auth()  # should not raise


def test_no_auth_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AuthorizationError) as exc_info:
        require_pentest_auth()
    assert "REVERSER_PENTEST_AUTHORIZED" in str(exc_info.value)
    assert ".reverser-authorized" in str(exc_info.value)


def test_env_var_other_value_does_not_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "0")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AuthorizationError):
        require_pentest_auth()
