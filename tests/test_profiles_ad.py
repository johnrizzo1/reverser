"""Regression tests for the AD profile registration."""

from reverser.profiles import PROFILES, get_profile, list_profiles


def test_ad_profile_registered():
    assert "ad" in PROFILES
    p = get_profile("ad")
    assert p.name == "Active Directory"
    assert "assumed-breach" in p.system_addendum.lower()


def test_ad_profile_has_all_eleven_skills():
    p = get_profile("ad")
    assert len(p.skills) == 11
    expected_names = {
        "Initial recon", "Identify DCs", "Spray known wordlist",
        "AS-REP roast", "Kerberoast", "Validate creds everywhere",
        "Collect BloodHound", "Find attack paths", "Dump secrets",
        "Show what we know", "Generate report",
    }
    actual_names = {s.name for s in p.skills}
    assert actual_names == expected_names


def test_ad_profile_skill_keys_unique():
    p = get_profile("ad")
    keys = [s.key for s in p.skills]
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"


def test_ad_profile_in_list_profiles():
    keys = {p.key for p in list_profiles()}
    assert "ad" in keys


def test_ad_prompt_mentions_key_tools():
    p = get_profile("ad")
    addendum = p.system_addendum
    for tool in [
        "kb_show", "kb_list_creds", "kb_add_finding", "kb_export_report",
        "netexec_smb", "netexec_winrm", "netexec_ldap", "netexec_mssql",
        "netexec_ssh", "netexec_ftp_wmi",
        "bloodhound_start", "bloodhound_collect", "bloodhound_canned",
        "bloodhound_query",
    ]:
        assert tool in addendum, f"missing tool reference: {tool}"


def test_ad_prompt_mentions_hypothesis_loop():
    p = get_profile("ad")
    assert "hypothesis" in p.system_addendum.lower()
    assert "5 tool calls" in p.system_addendum or "every 5" in p.system_addendum.lower()
