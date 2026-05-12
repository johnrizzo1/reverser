"""Tests for the searchsploit_search MCP tool."""

import json
import asyncio
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


_FAKE_SEARCHSPLOIT_JSON = json.dumps({
    "SEARCH": "ProFTPD",
    "DB_PATH": "/usr/share/exploitdb",
    "RESULTS_EXPLOIT": [
        {
            "EDB-ID": "49908",
            "Title": "ProFTPd 1.3.5 - 'mod_copy' Remote Command Execution",
            "Type": "remote",
            "Platform": "linux",
            "Date_Published": "2019-07-19",
            "Path": "linux/remote/49908.rb",
            "Codes": "CVE-2015-3306",
        },
        {
            "EDB-ID": "36742",
            "Title": "ProFTPd 1.3.5 - File Copy",
            "Type": "remote",
            "Platform": "linux",
            "Date_Published": "2015-04-07",
            "Path": "linux/remote/36742.txt",
            "Codes": "CVE-2015-3306",
        }
    ],
    "RESULTS_SHELLCODE": [],
})


def test_searchsploit_search_parses_results(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "49908" in text
    assert "ProFTPd 1.3.5" in text
    assert "CVE-2015-3306" in text


def test_searchsploit_search_no_results(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    empty = json.dumps({"SEARCH": "nonexistent", "DB_PATH": "/usr/share/exploitdb",
                        "RESULTS_EXPLOIT": [], "RESULTS_SHELLCODE": []})
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": empty, "stderr": "", "returncode": 0}
        result = _call(searchsploit_search, {"query": "nonexistent"})
    text = result["content"][0]["text"]
    assert "no" in text.lower() or "0" in text


def test_searchsploit_search_with_target_writes_kb_note(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        _call(searchsploit_search, {"query": "ProFTPD", "target": "10.10.10.5"})
    kb = for_target("10.10.10.5")
    notes = kb.get_notes()
    assert any("searchsploit" in n.lower() and "ProFTPD" in n for n in notes)


def test_searchsploit_search_without_target_no_kb_write(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        _call(searchsploit_search, {"query": "ProFTPD"})
    # If we never asked KB about this target, the KB cache shouldn't have it.
    # Be defensive: even if it does, just check notes is empty.
    import reverser.kb
    if "10.10.10.5" in reverser.kb._kb_cache:
        kb = reverser.kb._kb_cache["10.10.10.5"]
        assert kb.get_notes() == []


def test_searchsploit_search_command_failure(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": "", "stderr": "searchsploit: not found",
                                 "returncode": 127}
        result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is True
    assert "searchsploit" in result["content"][0]["text"]


def test_searchsploit_search_limit_truncates(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    many_results = {
        "SEARCH": "Linux", "DB_PATH": "/usr/share/exploitdb",
        "RESULTS_EXPLOIT": [
            {"EDB-ID": str(i), "Title": f"Result {i}", "Type": "local",
             "Platform": "linux", "Date_Published": "2020-01-01",
             "Path": f"linux/local/{i}.py", "Codes": ""}
            for i in range(100)
        ],
        "RESULTS_SHELLCODE": [],
    }
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": json.dumps(many_results), "stderr": "",
                                 "returncode": 0}
        result = _call(searchsploit_search, {"query": "Linux", "limit": 5})
    text = result["content"][0]["text"]
    # 5 lines of results plus header/summary; should mention 100 total
    assert "Result 0" in text
    assert "Result 4" in text
    assert "Result 5" not in text
    assert "100" in text  # mentions total


def test_searchsploit_requires_pentest_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import searchsploit_search
    result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()
