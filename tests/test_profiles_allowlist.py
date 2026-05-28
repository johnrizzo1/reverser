"""Tests for the Profile.tools_allowlist field."""

from reverser.profiles import (
    Profile,
    Skill,
    get_profile,
    is_network_profile,
    is_web_profile,
    profile_domain,
)


def test_default_tools_allowlist_is_none():
    """Profile with no tools_allowlist defaults to None (= all tools)."""
    p = Profile(name="x", key="x", description="x", system_addendum="x")
    assert p.tools_allowlist is None


def test_existing_profiles_have_no_allowlist():
    """All currently-shipped profiles default to None (full tool surface)."""
    # All 13 existing profiles must have tools_allowlist == None to preserve
    # current behavior. The manager profile (added later) will set this.
    for key in (
        "general", "linux", "windows", "android", "chrome", "managed",
        "api", "pentest", "webpentest", "webapi", "webrecon", "ad", "ctf",
    ):
        p = get_profile(key)
        assert p.tools_allowlist is None, f"{key} should have tools_allowlist=None"


def test_allowlist_can_be_set():
    """Profile accepts an explicit allowlist."""
    p = Profile(
        name="x", key="x", description="x", system_addendum="x",
        tools_allowlist=["mcp__re__kb_show", "mcp__re__bash"],
    )
    assert p.tools_allowlist == ["mcp__re__kb_show", "mcp__re__bash"]


def test_profile_domain_defaults_to_binary():
    p = Profile(name="x", key="x", description="x", system_addendum="x")
    assert p.domain == "binary"
    assert profile_domain("x", registry={"x": p}) == "binary"


def test_profile_domain_helpers_use_registry_metadata():
    assert is_web_profile("webpentest")
    assert is_network_profile("manager")
    assert is_network_profile("webapi")
    assert not is_network_profile("general")
