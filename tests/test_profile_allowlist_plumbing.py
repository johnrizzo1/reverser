"""Verify Profile.tools_allowlist propagates into ClaudeAgentOptions."""

import asyncio
from unittest.mock import patch

from reverser.profiles import Profile, get_profile


def test_backend_accepts_allowed_tools_override():
    """ClaudeBackend.run can take an allowed_tools list and uses it."""
    from reverser.backends.claude import ClaudeBackend

    backend = ClaudeBackend(tools=[])
    captured = {}

    async def fake_query(prompt, options):
        captured["options"] = options
        if False:
            yield  # type: ignore  # never reached, makes this an async iterator

    with patch("reverser.backends.claude.query", fake_query):
        async def drive():
            async for _ in backend.run(
                prompt="x",
                system_prompt="x",
                allowed_tools=["mcp__re__kb_show", "mcp__re__bash"],
            ):
                pass
        asyncio.new_event_loop().run_until_complete(drive())

    assert captured["options"].allowed_tools == ["mcp__re__kb_show", "mcp__re__bash"]


def test_backend_defaults_to_wildcard_when_no_override():
    """Default behavior is preserved: allowed_tools=['mcp__re__*']."""
    from reverser.backends.claude import ClaudeBackend

    backend = ClaudeBackend(tools=[])
    captured = {}

    async def fake_query(prompt, options):
        captured["options"] = options
        if False:
            yield  # type: ignore

    with patch("reverser.backends.claude.query", fake_query):
        async def drive():
            async for _ in backend.run(prompt="x", system_prompt="x"):
                pass
        asyncio.new_event_loop().run_until_complete(drive())

    assert captured["options"].allowed_tools == ["mcp__re__*"]


def test_existing_profiles_have_no_allowlist_so_default_applies():
    """All currently-shipped profiles default to None — existing call sites
    pass None and the backend resolves it to the wildcard."""
    profile = get_profile("general")
    assert profile.tools_allowlist is None


def test_synthetic_profile_with_allowlist_holds_the_list():
    custom = Profile(
        name="t", key="t", description="t", system_addendum="t",
        tools_allowlist=["mcp__re__kb_show"],
    )
    assert custom.tools_allowlist == ["mcp__re__kb_show"]
