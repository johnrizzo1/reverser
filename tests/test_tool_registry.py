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


def test_all_six_bloodhound_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "bloodhound_start" in names
    assert "bloodhound_stop" in names
    assert "bloodhound_status" in names
    assert "bloodhound_collect" in names
    assert "bloodhound_canned" in names
    assert "bloodhound_query" in names


def test_bloodhound_tools_count():
    bh_names = {t.name for t in ALL_TOOLS if t.name.startswith("bloodhound_")}
    assert len(bh_names) == 6


def test_dispatch_specialist_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "dispatch_specialist" in names


def test_all_hypothesis_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "kb_add_hypothesis" in names
    assert "kb_update_hypothesis" in names
    assert "kb_list_hypotheses" in names
    assert "kb_get_hypothesis" in names


def test_all_tools_count_after_manager_work():
    """Total registered tools after the manager-profile + post-merge additions.

    Baseline ALL_TOOLS list had 63 entries (61 unique — `nmap_scan` and
    `nikto_scan` were each registered twice as a pre-existing quirk).
    Manager work added 4 hypothesis CRUD + 1 dispatch_specialist = 68
    registered (66 unique). enum4linux_ng added post-merge = 69 registered,
    67 unique. web_browser_* tools (14) added = 91 registered, 89 unique.
    """
    assert len(ALL_TOOLS) == 91, (
        f"expected 91 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 89, (
        f"expected 89 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )


def test_enum4linux_ng_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "enum4linux_ng" in names


def test_searchsploit_search_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "searchsploit_search" in names


def test_msfvenom_generate_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "msfvenom_generate" in names


def test_metasploit_lifecycle_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "metasploit_start" in names
    assert "metasploit_stop" in names
    assert "metasploit_status" in names


def test_metasploit_operational_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "metasploit_search" in names
    assert "metasploit_run" in names
    assert "metasploit_session" in names


def test_all_eight_metasploit_bridge_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    for name in ("searchsploit_search", "msfvenom_generate",
                 "metasploit_start", "metasploit_stop", "metasploit_status",
                 "metasploit_search", "metasploit_run", "metasploit_session"):
        assert name in names, f"missing tool: {name}"


def test_all_web_browser_tools_registered():
    """All 14 web_browser_* tools are in the registry."""
    names = {t.name for t in ALL_TOOLS}
    expected = {
        "web_browser_start", "web_browser_status", "web_browser_close",
        "web_browser_navigate", "web_browser_click", "web_browser_type",
        "web_browser_fill_form", "web_browser_evaluate", "web_browser_wait_for",
        "web_browser_snapshot", "web_browser_network_log",
        "web_browser_capture_finding", "web_browser_confirm_xss", "web_browser_crawl",
    }
    missing = expected - names
    assert not missing, f"missing web_browser tools: {missing}"
