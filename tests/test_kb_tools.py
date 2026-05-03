"""Tests for KB read-side and editorial MCP tools."""

import asyncio
import pytest

from reverser.kb import for_target, HostFact, ServiceFact, CredentialFact, FindingFact


@pytest.fixture(autouse=True)
def authorize(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def _call_tool(tool_obj, args):
    """Invoke an SDK tool object's underlying coroutine.

    The claude_agent_sdk @tool decorator returns an SdkMcpTool whose callable
    lives on .handler. Fall back to .fn or calling the object directly for
    forward/backward compatibility.
    """
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_kb_show_with_explicit_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", os="Windows", is_dc=True))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_finding(FindingFact(title="t", severity="high", description="x"))
    result = _call_tool(kb_show, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "Hosts: 1" in text
    assert "Credentials:" in text
    assert "valid" in text
    assert "high" in text


def test_kb_show_defaults_to_sole_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    for_target("10.10.10.5")
    result = _call_tool(kb_show, {"target": ""})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text


def test_kb_show_errors_on_no_targets(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    result = _call_tool(kb_show, {"target": ""})
    assert result.get("is_error")


def test_kb_show_errors_on_multiple_no_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    for_target("10.10.10.5")
    for_target("10.10.10.6")
    result = _call_tool(kb_show, {"target": ""})
    assert result.get("is_error")
    assert "10.10.10.5" in result["content"][0]["text"]
    assert "10.10.10.6" in result["content"][0]["text"]


def test_kb_list_hosts(tmp_targets_dir):
    from reverser.tools.kb import kb_list_hosts
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows",
                            domain="CORP", is_dc=True, smb_signing="required"))
    kb.record_host(HostFact(ip="10.10.10.6", hostname="ws01", os="Windows 10"))
    result = _call_tool(kb_list_hosts, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "dc01" in text
    assert "10.10.10.6" in text
    assert "ws01" in text
    assert "required" in text


def test_kb_list_hosts_empty(tmp_targets_dir):
    from reverser.tools.kb import kb_list_hosts
    for_target("10.10.10.5")
    result = _call_tool(kb_list_hosts, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "No hosts" in text or "0 hosts" in text or "(no rows)" in text or "0 rows" in text


def test_kb_list_services_all(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp",
                                  service="microsoft-ds", version="Windows Server 2019",
                                  scan_source="nmap_scan"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp",
                                  service="ssh", version="OpenSSH 8.4"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "microsoft-ds" in text
    assert "22" in text
    assert "ssh" in text


def test_kb_list_services_filter_by_port(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5", "port": 445})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "22" not in text


def test_kb_list_services_filter_by_host(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.6"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.6", port=22, proto="tcp"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5", "host": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "10.10.10.6" not in text or "22" not in text


def test_kb_list_creds_all(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    from reverser.kb import CredResult
    kb = for_target("10.10.10.5")
    cid = kb.record_credential(CredentialFact(
        username="jdoe", password="x", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    kb.record_cred_result(cid, CredResult(service_kind="smb", target_host="10.10.10.5", success=True))
    kb.record_credential(CredentialFact(username="bob", password="y", status="invalid"))
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "jdoe" in text
    assert "bob" in text
    assert "valid" in text
    assert "smb" in text


def test_kb_list_creds_filter_by_status(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="bob", password="y", status="invalid"))
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5", "status": "valid"})
    text = result["content"][0]["text"]
    assert "jdoe" in text
    assert "bob" not in text


def test_kb_list_creds_empty(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    for_target("10.10.10.5")
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "No credentials" in text or "(no rows)" in text or "0 rows" in text


def test_kb_add_finding_basic(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "SMB signing not required",
        "severity": "medium",
        "description": "Allows NTLM relay attacks.",
    })
    text = result["content"][0]["text"]
    assert "added" in text.lower() or "id=" in text.lower()
    findings = for_target("10.10.10.5").get_findings()
    assert len(findings) == 1
    assert findings[0].title == "SMB signing not required"


def test_kb_add_finding_with_evidence_and_cvss(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "Zerologon",
        "severity": "critical",
        "description": "CVE-2020-1472",
        "evidence_paths": ["findings/zerologon.txt"],
        "cvss": 10.0,
    })
    assert not result.get("is_error")
    f = for_target("10.10.10.5").get_findings()[0]
    assert f.cvss == 10.0
    assert f.evidence_paths == ["findings/zerologon.txt"]


def test_kb_add_finding_invalid_severity(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "x",
        "severity": "emergency",
        "description": "x",
    })
    assert result.get("is_error")
