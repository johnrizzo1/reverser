"""End-to-end smoke test exercising the full KB public API."""

from reverser.kb import (
    for_target, list_targets,
    HostFact, ServiceFact, CredentialFact, FindingFact, ArtifactFact, CredResult,
)
import reverser.kb


def test_full_engagement_flow(tmp_targets_dir):
    # Clear any cached KB instances from prior tests
    reverser.kb._kb_cache.clear()

    # Initial recon: discover host, services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows", is_dc=True))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp",
                                  service="microsoft-ds", scan_source="nmap_scan"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=389, proto="tcp",
                                  service="ldap", scan_source="nmap_scan"))

    # Spray finds a valid cred
    cred_id = kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    kb.record_cred_result(cred_id, CredResult(
        service_kind="smb", target_host="10.10.10.5", success=True,
    ))

    # Same cred validated against winrm
    kb.record_cred_result(cred_id, CredResult(
        service_kind="winrm", target_host="10.10.10.5", success=True,
    ))

    # Drop a finding
    kb.record_finding(FindingFact(
        title="SMB signing not required",
        severity="medium",
        description="Allows NTLM relay attacks.",
    ))

    # Reopen the KB (simulating a new tool call) and verify state
    kb2 = for_target("10.10.10.5")
    assert kb2 is kb  # cache hit

    hosts = kb2.get_hosts()
    assert len(hosts) == 1 and hosts[0].is_dc

    services = kb2.get_services()
    assert {s.port for s in services} == {445, 389}

    valid_creds = kb2.get_credentials(status="valid")
    assert len(valid_creds) == 1 and valid_creds[0].username == "jdoe"

    results = kb2.get_cred_results(cred_id)
    assert {r.service_kind for r in results} == {"smb", "winrm"}

    findings = kb2.get_findings()
    assert len(findings) == 1

    assert "10.10.10.5" in list_targets()
