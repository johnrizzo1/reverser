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
    kb = KB("10.10.10.5")
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
