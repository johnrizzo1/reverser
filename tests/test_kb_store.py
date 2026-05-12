"""Tests for the KB store public API."""

import pytest

from reverser.kb.store import HostFact, ServiceFact, CredentialFact, FindingFact, CredResult, ArtifactFact


def test_host_fact_minimal():
    h = HostFact(ip="10.10.10.5")
    assert h.ip == "10.10.10.5"
    assert h.hostname is None
    assert h.is_dc is False


def test_host_fact_full():
    h = HostFact(
        ip="10.10.10.5",
        hostname="dc01",
        os="Windows Server 2019",
        domain="CORP.LOCAL",
        is_dc=True,
        smb_signing="required",
    )
    assert h.is_dc is True
    assert h.smb_signing == "required"


def test_service_fact_minimal():
    s = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp")
    assert s.port == 445
    assert s.proto == "tcp"
    assert s.service is None


def test_credential_fact_password():
    c = CredentialFact(username="jdoe", password="Summer2026!", domain="CORP")
    assert c.password == "Summer2026!"
    assert c.nt_hash is None
    assert c.status == "untested"


def test_credential_fact_hash():
    c = CredentialFact(username="jdoe", nt_hash="aad3b4...", status="valid")
    assert c.password is None
    assert c.nt_hash == "aad3b4..."
    assert c.status == "valid"


def test_credential_fact_invalid_status_raises():
    with pytest.raises(ValueError):
        CredentialFact(username="jdoe", password="x", status="bogus")


def test_finding_fact_severity_validation():
    FindingFact(title="Test", severity="high", description="x")
    with pytest.raises(ValueError):
        FindingFact(title="Test", severity="emergency", description="x")


from reverser.kb.store import KB, normalize_target


def test_normalize_target_lowercase_strip():
    assert normalize_target("  10.10.10.5  ") == "10.10.10.5"
    assert normalize_target("DC01.CORP.LOCAL") == "dc01.corp.local"


def test_normalize_target_empty_raises():
    with pytest.raises(ValueError):
        normalize_target("")
    with pytest.raises(ValueError):
        normalize_target("   ")


def test_kb_creates_target_dir(tmp_targets_dir):
    KB("10.10.10.5")
    assert (tmp_targets_dir / "10.10.10.5").is_dir()
    assert (tmp_targets_dir / "10.10.10.5" / "state.db").is_file()


def test_kb_creates_subdirs(tmp_targets_dir):
    KB("10.10.10.5")
    assert (tmp_targets_dir / "10.10.10.5" / "findings").is_dir()
    assert (tmp_targets_dir / "10.10.10.5" / "loot").is_dir()


def test_kb_target_id_normalized(tmp_targets_dir):
    kb = KB("  10.10.10.5  ")
    assert kb.target_id == "10.10.10.5"
    assert (tmp_targets_dir / "10.10.10.5").is_dir()


def test_kb_records_target_row(tmp_targets_dir):
    kb = KB("10.10.10.5")
    with kb._connect() as conn:
        row = conn.execute("SELECT id FROM targets WHERE id = ?", ("10.10.10.5",)).fetchone()
        assert row is not None
        assert row[0] == "10.10.10.5"


def test_record_host_basic(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows", is_dc=True))
    hosts = kb.get_hosts()
    assert len(hosts) == 1
    assert hosts[0].ip == "10.10.10.5"
    assert hosts[0].hostname == "dc01"
    assert hosts[0].is_dc is True


def test_record_host_idempotent(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01"))
    hosts = kb.get_hosts()
    assert len(hosts) == 1
    assert hosts[0].hostname == "dc01"


def test_record_host_preserves_fields_when_none(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows"))
    kb.record_host(HostFact(ip="10.10.10.5", domain="CORP.LOCAL"))
    hosts = kb.get_hosts()
    assert hosts[0].hostname == "dc01"
    assert hosts[0].os == "Windows"
    assert hosts[0].domain == "CORP.LOCAL"


def test_record_service_basic(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(
        host_ip="10.10.10.5", port=445, proto="tcp",
        service="microsoft-ds", version="Windows Server 2019",
        scan_source="nmap_scan",
    ))
    svcs = kb.get_services()
    assert len(svcs) == 1
    assert svcs[0].port == 445
    assert svcs[0].service == "microsoft-ds"


def test_record_service_idempotent(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    s1 = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp", service="smb")
    s2 = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp", service="microsoft-ds", version="2019")
    kb.record_service(s1)
    kb.record_service(s2)
    svcs = kb.get_services()
    assert len(svcs) == 1
    assert svcs[0].service == "microsoft-ds"
    assert svcs[0].version == "2019"


def test_get_services_filter_by_host(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.6"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.6", port=22, proto="tcp"))
    assert len(kb.get_services(host_ip="10.10.10.5")) == 1
    assert kb.get_services(host_ip="10.10.10.5")[0].port == 445


def test_get_services_filter_by_port(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp"))
    assert len(kb.get_services(port=445)) == 1


def test_record_credential_new(tmp_targets_dir):
    kb = KB("10.10.10.5")
    cred_id = kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    assert cred_id > 0
    creds = kb.get_credentials()
    assert len(creds) == 1
    assert creds[0].username == "jdoe"
    assert creds[0].status == "valid"


def test_record_credential_dedup_returns_same_id(tmp_targets_dir):
    kb = KB("10.10.10.5")
    c = CredentialFact(username="jdoe", password="x", status="untested")
    id1 = kb.record_credential(c)
    id2 = kb.record_credential(c)
    assert id1 == id2
    assert len(kb.get_credentials()) == 1


def test_record_credential_status_upgrade(tmp_targets_dir):
    """Re-recording an existing cred with status=valid must upgrade from untested."""
    kb = KB("10.10.10.5")
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="untested"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    creds = kb.get_credentials()
    assert len(creds) == 1
    assert creds[0].status == "valid"


def test_record_credential_status_no_downgrade(tmp_targets_dir):
    """Once valid, must not be downgraded to untested or invalid by a later record."""
    kb = KB("10.10.10.5")
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="invalid"))
    creds = kb.get_credentials()
    assert creds[0].status == "valid"


def test_get_credentials_filter_by_status(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_credential(CredentialFact(username="a", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="b", password="y", status="invalid"))
    valid = kb.get_credentials(status="valid")
    assert len(valid) == 1
    assert valid[0].username == "a"


def test_credential_hash_distinct_from_password(tmp_targets_dir):
    """Same user with different cred material is recorded as separate rows."""
    kb = KB("10.10.10.5")
    kb.record_credential(CredentialFact(username="jdoe", password="x"))
    kb.record_credential(CredentialFact(username="jdoe", nt_hash="aad3..."))
    assert len(kb.get_credentials()) == 2


def test_record_cred_result(tmp_targets_dir):
    kb = KB("10.10.10.5")
    cred_id = kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_cred_result(cred_id, CredResult(
        service_kind="smb", target_host="10.10.10.5", success=True,
    ))
    results = kb.get_cred_results(cred_id)
    assert len(results) == 1
    assert results[0].service_kind == "smb"
    assert results[0].success is True


def test_get_cred_results_for_cred(tmp_targets_dir):
    kb = KB("10.10.10.5")
    cid = kb.record_credential(CredentialFact(username="jdoe", password="x"))
    kb.record_cred_result(cid, CredResult(service_kind="smb", target_host="10.10.10.5", success=True))
    kb.record_cred_result(cid, CredResult(service_kind="winrm", target_host="10.10.10.5", success=False, error_msg="STATUS_LOGON_FAILURE"))
    results = kb.get_cred_results(cid)
    assert len(results) == 2


def test_record_finding(tmp_targets_dir):
    kb = KB("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Anonymous SMB share access",
        severity="medium",
        description="The IPC$ share allows anonymous enumeration.",
        evidence_paths=["findings/smb_anon.txt"],
    ))
    assert fid > 0
    findings = kb.get_findings()
    assert len(findings) == 1
    assert findings[0].title == "Anonymous SMB share access"
    assert findings[0].severity == "medium"
    assert findings[0].evidence_paths == ["findings/smb_anon.txt"]


def test_record_finding_with_cvss(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_finding(FindingFact(
        title="CVE-2020-1472", severity="critical",
        description="Zerologon", cvss=10.0,
    ))
    f = kb.get_findings()[0]
    assert f.cvss == 10.0


def test_record_artifact(tmp_targets_dir):
    kb = KB("10.10.10.5")
    aid = kb.record_artifact(ArtifactFact(
        kind="asreproast_hashes", path="loot/asrep_hashes.txt",
        sha256="abc123", source_tool="kerberos_enum",
    ))
    assert aid > 0
    arts = kb.get_artifacts()
    assert len(arts) == 1
    assert arts[0].kind == "asreproast_hashes"


def test_record_note(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_note("Initial recon — saw OpenSSH 7.9 on FreeBSD")
    notes = kb.get_notes()
    assert len(notes) == 1
    assert "OpenSSH" in notes[0]


def test_get_findings_filter_by_severity(tmp_targets_dir):
    kb = KB("10.10.10.5")
    kb.record_finding(FindingFact(title="a", severity="low", description="x"))
    kb.record_finding(FindingFact(title="b", severity="high", description="x"))
    high = kb.get_findings(severity="high")
    assert len(high) == 1
    assert high[0].title == "b"


def test_for_target_returns_kb(tmp_targets_dir):
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    assert isinstance(kb, KB)
    assert kb.target_id == "10.10.10.5"


def test_for_target_caches_per_target(tmp_targets_dir):
    """Calling for_target twice with the same target should return the same instance."""
    from reverser.kb import for_target
    kb1 = for_target("10.10.10.5")
    kb2 = for_target("10.10.10.5")
    assert kb1 is kb2


def test_for_target_normalizes(tmp_targets_dir):
    from reverser.kb import for_target
    kb1 = for_target("10.10.10.5")
    kb2 = for_target("  10.10.10.5  ")
    assert kb1 is kb2


def test_list_targets_empty(tmp_targets_dir):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb import list_targets
    assert list_targets() == []


def test_list_targets_returns_existing(tmp_targets_dir):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb import for_target, list_targets
    for_target("10.10.10.5")
    for_target("dc01.corp.local")
    targets = list_targets()
    assert sorted(targets) == ["10.10.10.5", "dc01.corp.local"]


def test_list_targets_ignores_non_target_dirs(tmp_targets_dir):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb import for_target, list_targets
    for_target("10.10.10.5")
    (tmp_targets_dir / "junk").mkdir()
    assert list_targets() == ["10.10.10.5"]


def test_append_finding_evidence_to_existing_finding(tmp_targets_dir):
    """append_finding_evidence adds a path to an existing finding's evidence_paths."""
    from reverser.kb import for_target, FindingFact

    kb = for_target("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Test finding",
        severity="high",
        description="Body",
        evidence_paths=["existing/path.txt"],
    ))

    kb.append_finding_evidence(fid, "targets/10.10.10.5/findings/1/screenshot-1.png")

    findings = kb.get_findings()
    assert len(findings) == 1
    assert findings[0].evidence_paths == [
        "existing/path.txt",
        "targets/10.10.10.5/findings/1/screenshot-1.png",
    ]


def test_append_finding_evidence_to_finding_with_no_existing_evidence(tmp_targets_dir):
    """append_finding_evidence works when evidence_paths is empty."""
    from reverser.kb import for_target, FindingFact

    kb = for_target("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Bare finding",
        severity="low",
        description="No initial evidence",
    ))

    kb.append_finding_evidence(fid, "first/screenshot.png")

    findings = kb.get_findings()
    assert findings[0].evidence_paths == ["first/screenshot.png"]


def test_append_finding_evidence_raises_for_unknown_finding(tmp_targets_dir):
    """append_finding_evidence raises ValueError if finding_id doesn't exist."""
    import pytest
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    with pytest.raises(ValueError, match="No finding with id="):
        kb.append_finding_evidence(99999, "some/path.png")


def test_append_finding_evidence_preserves_order(tmp_targets_dir):
    """Multiple appends maintain insertion order."""
    from reverser.kb import for_target, FindingFact

    kb = for_target("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Multi-evidence finding", severity="medium", description="",
    ))

    kb.append_finding_evidence(fid, "a.png")
    kb.append_finding_evidence(fid, "b.png")
    kb.append_finding_evidence(fid, "c.png")

    findings = kb.get_findings()
    assert findings[0].evidence_paths == ["a.png", "b.png", "c.png"]
