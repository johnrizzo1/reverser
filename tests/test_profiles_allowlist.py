"""Tests for the Profile.tools_allowlist field."""

from reverser.profiles import Profile, Skill, get_profile


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
