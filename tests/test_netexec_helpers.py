"""Unit tests for the netexec module's shared helpers."""

from reverser.tools.netexec import (
    DEFAULT_SPRAY_MAX,
    ResolvedCredential,
    _auth_succeeded,
    _build_auth_args,
    _check_spray_allowed,
    _parse_nxc_ldap_computers,
    _parse_nxc_secret_dump,
    _parse_nxc_share_table,
    _parse_nxc_status_line,
    _resolve_credential,
    _save_dump_artifact,
    _spray_max,
)


def test_resolve_credential_explicit_password(tmp_targets_dir):
    cred, err = _resolve_credential("10.10.10.5", "jdoe", "pw", None, "CORP")
    assert err is None
    assert cred.username == "jdoe"
    assert cred.password == "pw"
    assert cred.domain == "CORP"
    assert cred.origin == "explicit args"


def test_resolve_credential_explicit_hash(tmp_targets_dir):
    cred, err = _resolve_credential("10.10.10.5", "jdoe", None, "aad3b...", None)
    assert err is None
    assert cred.nt_hash == "aad3b..."
    assert cred.password is None


def test_resolve_credential_no_creds_no_kb(tmp_targets_dir):
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert cred is None
    assert "No credentials supplied" in err
    assert "no valid credentials in KB" in err


def test_resolve_credential_falls_back_to_kb(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert err is None
    assert cred.username == "jdoe"
    assert cred.password == "Summer2026!"
    assert cred.domain == "CORP"
    assert "[KB] Using credential: jdoe" in cred.origin


def test_resolve_credential_picks_most_recent_valid(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(username="alice", password="a", status="valid"))
    kb.record_credential(CredentialFact(username="bob", password="b", status="valid"))
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert err is None
    assert cred.username == "bob"


def test_spray_blocked_by_default(monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    err = _check_spray_allowed()
    assert err is not None
    assert "REVERSER_AD_ALLOW_SPRAY" in err
    assert "REVERSER_SPRAY_MAX" in err


def test_spray_allowed_with_env(monkeypatch):
    monkeypatch.setenv("REVERSER_AD_ALLOW_SPRAY", "1")
    assert _check_spray_allowed() is None


def test_spray_max_default(monkeypatch):
    monkeypatch.delenv("REVERSER_SPRAY_MAX", raising=False)
    assert _spray_max() == DEFAULT_SPRAY_MAX


def test_spray_max_override(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "5")
    assert _spray_max() == 5


def test_spray_max_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "abc")
    assert _spray_max() == DEFAULT_SPRAY_MAX


def test_spray_max_negative_falls_back(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "-1")
    assert _spray_max() == DEFAULT_SPRAY_MAX


def test_save_dump_artifact_creates_file(tmp_targets_dir):
    path, sha = _save_dump_artifact("10.10.10.5", "ntds_dump", "Administrator:500:aad3...:8846...:::\n")
    assert path.exists()
    assert path.read_text().startswith("Administrator:500:")
    assert path.parent.name == "loot"
    assert path.name.startswith("ntds_dump_")
    assert path.name.endswith(".txt")
    assert len(sha) == 64


def test_parse_nxc_status_line_success():
    line = "SMB         10.10.10.5      445    DC01             [+] CORP.LOCAL\\jdoe:Summer2026! (Pwn3d!)"
    parsed = _parse_nxc_status_line(line)
    assert parsed is not None
    assert parsed["proto"] == "SMB"
    assert parsed["ip"] == "10.10.10.5"
    assert parsed["port"] == 445
    assert parsed["host"] == "DC01"
    assert parsed["sign"] == "+"
    assert "Pwn3d" in parsed["rest"]


def test_parse_nxc_status_line_failure():
    line = "SMB    10.10.10.5    445    DC01    [-] CORP.LOCAL\\jdoe:bad STATUS_LOGON_FAILURE"
    parsed = _parse_nxc_status_line(line)
    assert parsed is not None
    assert parsed["sign"] == "-"


def test_parse_nxc_status_line_unparseable():
    assert _parse_nxc_status_line("garbage line") is None
    assert _parse_nxc_status_line("") is None


def test_auth_succeeded_true():
    out = (
        "SMB    10.10.10.5    445    DC01    [*] Windows Server 2019\n"
        "SMB    10.10.10.5    445    DC01    [+] CORP.LOCAL\\jdoe:Summer2026!\n"
    )
    assert _auth_succeeded(out) is True


def test_auth_succeeded_false():
    out = (
        "SMB    10.10.10.5    445    DC01    [*] Windows Server 2019\n"
        "SMB    10.10.10.5    445    DC01    [-] CORP.LOCAL\\jdoe:bad\n"
    )
    assert _auth_succeeded(out) is False


def test_parse_nxc_share_table():
    out = (
        "SMB    10.10.10.5    445   DC01   [*] Enumerated shares\n"
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
        "SMB    10.10.10.5    445   DC01   NETLOGON        READ            Logon server share\n"
    )
    rows = _parse_nxc_share_table(out)
    names = [r["share"] for r in rows]
    assert "ADMIN$" in names
    assert "IPC$" in names
    assert "NETLOGON" in names
    admin = [r for r in rows if r["share"] == "ADMIN$"][0]
    assert "READ" in admin["perms"]
    assert "WRITE" in admin["perms"]


def test_parse_nxc_ldap_computers():
    out = (
        "LDAP    10.10.10.5   389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.5   389   DC01   WS01.CORP.LOCAL\n"
        "LDAP    10.10.10.5   389   DC01   [*] noise line\n"
    )
    rows = _parse_nxc_ldap_computers(out)
    fqdns = [r["fqdn"] for r in rows]
    assert "DC01.CORP.LOCAL" in fqdns
    assert "WS01.CORP.LOCAL" in fqdns
    assert all(r["domain"] == "CORP.LOCAL" for r in rows)


def test_parse_nxc_secret_dump():
    out = (
        "[+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
        "garbage\n"
    )
    rows = _parse_nxc_secret_dump(out)
    assert len(rows) == 2
    users = [r["username"] for r in rows]
    assert "Administrator" in users
    assert "krbtgt" in users
    admin = [r for r in rows if r["username"] == "Administrator"][0]
    assert admin["rid"] == 500
    assert admin["nt_hash"] == "8846f7eaee8fb117ad06bdd830b7586c"


def test_build_auth_args_password():
    cred = ResolvedCredential(username="jdoe", password="pw", nt_hash=None, domain="CORP", origin="x")
    args = _build_auth_args(cred)
    assert args == ["-u", "jdoe", "-p", "pw", "-d", "CORP"]


def test_build_auth_args_hash_local():
    cred = ResolvedCredential(username="admin", password=None, nt_hash="aad3b...", domain=None, origin="x")
    args = _build_auth_args(cred, local_auth=True)
    assert args == ["-u", "admin", "-H", "aad3b...", "--local-auth"]


def test_build_auth_args_empty_password():
    cred = ResolvedCredential(username="guest", password="", nt_hash=None, domain=None, origin="x")
    args = _build_auth_args(cred)
    assert args == ["-u", "guest", "-p", ""]
