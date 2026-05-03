"""Tests for the KB store public API."""

import pytest

from reverser.kb.store import HostFact, ServiceFact, CredentialFact, FindingFact


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
