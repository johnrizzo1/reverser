"""Tests for the manager profile registration and shape."""

from reverser.profiles import PROFILES, get_profile


def test_manager_profile_registered():
    assert "manager" in PROFILES
    p = get_profile("manager")
    assert p.name  # non-empty
    assert p.description


def test_manager_has_six_skills():
    p = get_profile("manager")
    assert len(p.skills) == 6
    keys = sorted(s.key for s in p.skills)
    assert keys == sorted(["k", "s", "r", "p", "b", "w"])


def test_manager_has_explicit_tools_allowlist():
    p = get_profile("manager")
    assert p.tools_allowlist is not None
    assert isinstance(p.tools_allowlist, list)
    # Must include the dispatch tool
    assert "mcp__re__dispatch_specialist" in p.tools_allowlist
    # Must include hypothesis tools
    assert "mcp__re__kb_add_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_update_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_get_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_list_hypotheses" in p.tools_allowlist
    # Must include bash for ad-hoc commands
    assert "mcp__re__bash" in p.tools_allowlist
    # Must NOT include heavy offensive tools — they require dispatch
    forbidden = {
        "mcp__re__netexec_smb",
        "mcp__re__netexec_ldap",
        "mcp__re__bloodhound_collect",
        "mcp__re__sqlmap_test",
        "mcp__re__nuclei_scan",
    }
    overlap = forbidden & set(p.tools_allowlist)
    assert not overlap, f"manager allowlist must NOT include heavy tools: {overlap}"


def test_manager_system_addendum_mentions_dispatch_and_hypothesis():
    p = get_profile("manager")
    addendum = p.system_addendum.lower()
    assert "dispatch" in addendum
    assert "hypothes" in addendum  # hypothesis or hypotheses
    for specialty in ("ad", "pentest", "webpentest", "webapi", "webrecon"):
        assert specialty in addendum, f"specialty {specialty} not mentioned"
