"""Tests for bloodhound-python zip import + collect tool wrapper."""

import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from reverser.tools.bloodhound import (
    _import_bloodhound_zip,
    _classify_bloodhound_json_file,
    _CYPHER_BY_KIND,
)


def _make_bh_zip(tmp_path: Path, files: dict[str, dict]) -> Path:
    z = tmp_path / "bh.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, json.dumps(content))
    return z


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def test_classify_users_file():
    assert _classify_bloodhound_json_file("20260503000000_users.json") == "users"
    assert _classify_bloodhound_json_file("users.json") == "users"


def test_classify_computers_file():
    assert _classify_bloodhound_json_file("computers.json") == "computers"


def test_classify_groups_file():
    assert _classify_bloodhound_json_file("groups.json") == "groups"


def test_classify_unknown_file_returns_none():
    assert _classify_bloodhound_json_file("README.md") is None


def test_cypher_by_kind_has_all_six():
    assert set(_CYPHER_BY_KIND.keys()) == {
        "users", "computers", "groups", "ous", "gpos", "domains",
    }


def test_import_bloodhound_zip_runs_merges(tmp_path):
    payload = {
        "data": [
            {"ObjectIdentifier": "S-1-5-21-1", "Properties": {"name": "JDOE@CORP.LOCAL", "enabled": True}},
            {"ObjectIdentifier": "S-1-5-21-2", "Properties": {"name": "ASMITH@CORP.LOCAL", "enabled": False}},
        ],
        "meta": {"type": "users", "count": 2},
    }
    z = _make_bh_zip(tmp_path, {"users.json": payload})

    fake_session = MagicMock()
    fake_session.run = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    counts = _import_bloodhound_zip(fake_driver, z)
    assert counts == {"users": 2}
    assert fake_session.run.call_count >= 2


def test_import_bloodhound_zip_multiple_kinds(tmp_path):
    z = _make_bh_zip(tmp_path, {
        "users.json": {"data": [{"ObjectIdentifier": "S-1-1", "Properties": {"name": "u1@D"}}], "meta": {"type": "users"}},
        "computers.json": {"data": [{"ObjectIdentifier": "S-2-1", "Properties": {"name": "c1@D"}}], "meta": {"type": "computers"}},
        "groups.json": {"data": [], "meta": {"type": "groups"}},
        "README.md": {"ignore": "me"},
    })
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    counts = _import_bloodhound_zip(fake_driver, z)
    assert counts == {"users": 1, "computers": 1, "groups": 0}


def test_import_bloodhound_zip_handles_objectless_entries(tmp_path):
    z = _make_bh_zip(tmp_path, {
        "users.json": {
            "data": [{"Properties": {"name": "no_oid@D"}}],
            "meta": {"type": "users"},
        },
    })
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    counts = _import_bloodhound_zip(fake_driver, z)
    assert counts == {"users": 0}


def test_bloodhound_collect_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_collect
    result = _call(bloodhound_collect, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
    })
    assert result.get("is_error") is True


def test_bloodhound_collect_requires_neo4j_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_collect
    result = _call(bloodhound_collect, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
    })
    assert result.get("is_error") is True
    assert "bloodhound_start" in result["content"][0]["text"]


def test_bloodhound_collect_requires_password_or_hash(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    from reverser.tools.bloodhound import _write_pid, bloodhound_collect
    _write_pid("10.10.10.5", os.getpid())
    result = _call(bloodhound_collect, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe",
    })
    assert result.get("is_error") is True
    assert "password" in result["content"][0]["text"].lower() or "hash" in result["content"][0]["text"].lower()


def test_bloodhound_collect_invokes_bloodhound_python(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    from reverser.tools.bloodhound import _write_pid, bloodhound_collect
    _write_pid("10.10.10.5", os.getpid())

    captured_cmd = []

    def fake_run_bh(cmd, cwd):
        captured_cmd.extend(cmd)
        z = Path(cwd) / "20260503000000_corp.local.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("users.json", json.dumps({"data": [], "meta": {"type": "users"}}))
        return {"stdout": "ok", "stderr": "", "returncode": 0}

    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    with patch("reverser.tools.bloodhound._run_bloodhound_python", side_effect=fake_run_bh), \
         patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver), \
         patch("reverser.tools.bloodhound._set_meta"):
        result = _call(bloodhound_collect, {
            "target": "10.10.10.5", "domain": "CORP.LOCAL",
            "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
        })
    assert result.get("is_error") is not True
    assert "users" in result["content"][0]["text"].lower()
    assert "-d" in captured_cmd
    assert "CORP.LOCAL" in captured_cmd
    assert "-u" in captured_cmd
    assert "jdoe" in captured_cmd
