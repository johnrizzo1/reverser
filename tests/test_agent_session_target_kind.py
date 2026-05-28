"""Target-kind handling in AgentSession.

Network-profile sessions (manager/pentest/ad/exploit) must not treat the
target string as a binary path. Specifically:

  - The target value must NOT be resolved to an absolute filesystem path
    via Path.resolve() — that corrupts hostnames like "Helix" into
    "/cwd/Helix" and tricks the model into binary-RE behavior.
  - The system prompt must come from NETWORK_SYSTEM_PROMPT, not the
    binary-RE SYSTEM_PROMPT.
  - The per-message IMPORTANT line must describe the target as a
    host/service, not a binary path.

Regression for the bug where entering "Helix" (or any bare hostname)
under the `manager` profile caused the agent to start analyzing
"/Users/.../Helix" as a binary.
"""
import os
import pytest
from reverser.agent_session import AgentSession
from reverser.profiles import get_profile


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    """Network profiles require pentest authorization to construct."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def _mk(profile_key, target, tmp_path):
    return AgentSession(
        binary_path=target,
        profile=get_profile(profile_key),
        budget=5.0, max_turns=10,
        backend_name="lmstudio", model="x",
        log_path=str(tmp_path / "log.jsonl"),
    )


@pytest.mark.parametrize("profile_key,target", [
    ("manager", "Helix"),
    ("manager", "10.129.245.123"),
    ("pentest", "host01"),
    ("ad", "dc1.corp.local"),
    ("exploit", "10.10.10.5"),
])
def test_network_profile_does_not_resolve_target_to_filesystem_path(
    profile_key, target, tmp_path,
):
    sess = _mk(profile_key, target, tmp_path)
    # The bug was: bare names became absolute paths under cwd.
    assert sess.target == target, (
        f"network-profile target was resolved to a filesystem path: "
        f"{sess.target!r} (expected {target!r})"
    )
    assert sess._is_network is True
    assert not sess.target.startswith(os.getcwd()), sess.target


@pytest.mark.parametrize("profile_key,target", [
    ("manager", "Helix"),
    ("manager", "10.129.245.123"),
    ("pentest", "host01"),
    ("ad", "dc1.corp.local"),
    ("exploit", "10.10.10.5"),
])
def test_network_profile_uses_network_system_prompt(
    profile_key, target, tmp_path,
):
    sess = _mk(profile_key, target, tmp_path)
    sysp = sess._build_system_prompt()
    assert "expert network red-team operator" in sysp, (
        f"network profile got wrong base prompt; opens with: {sysp[:120]!r}"
    )
    # Must NOT carry the binary-RE opener
    assert "expert reverse engineer" not in sysp[:200], (
        f"network profile leaked binary-RE base prompt: {sysp[:200]!r}"
    )


@pytest.mark.parametrize("profile_key,target", [
    ("manager", "Helix"),
    ("manager", "10.129.245.123"),
    ("pentest", "host01"),
])
def test_network_profile_per_message_important_line_says_host_service(
    profile_key, target, tmp_path,
):
    sess = _mk(profile_key, target, tmp_path)
    msgp = sess._build_prompt("hi")
    important = next(
        (ln for ln in msgp.split("\n") if "IMPORTANT" in ln), None,
    )
    assert important is not None, msgp
    assert "host/service" in important, important
    assert "binary path" not in important, important
    assert "target directory" not in important, important


def test_binary_profile_still_resolves_to_filesystem_path(tmp_path):
    """Regression guard: binary profiles must still resolve to abs path."""
    binary = tmp_path / "noop"
    binary.write_bytes(b"")
    sess = AgentSession(
        binary_path=str(binary),
        profile=get_profile("general"),
        log_path=str(tmp_path / "log.jsonl"),
    )
    assert sess.target == str(binary.resolve())
    assert sess._is_network is False
    sysp = sess._build_system_prompt()
    assert "expert reverse engineer" in sysp
