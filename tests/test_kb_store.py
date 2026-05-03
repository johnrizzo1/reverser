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
