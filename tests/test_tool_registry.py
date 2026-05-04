"""Verify netexec tools are exposed via the MCP server registry."""

from reverser.tools import ALL_TOOLS


def test_all_six_netexec_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "netexec_smb" in names
    assert "netexec_winrm" in names
    assert "netexec_ldap" in names
    assert "netexec_mssql" in names
    assert "netexec_ssh" in names
    assert "netexec_ftp_wmi" in names


def test_netexec_tools_count():
    netexec_names = {t.name for t in ALL_TOOLS if t.name.startswith("netexec_")}
    assert len(netexec_names) == 6
