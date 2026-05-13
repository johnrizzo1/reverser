"""Tests for the dispatch_specialist sub-agent event callback.

The TUI sets `AgentSession.on_dispatch_event` per turn so dispatch_specialist
can surface its sub-agent's thinking / tool_call / tool_result / text events
in the chat log with a `[specialty]` prefix. The callback contract is:

  - None means "do not render" (CLI-only contexts)
  - When set, called with (specialty, kind, content) for each sub-agent event
  - Exceptions raised by the callback are swallowed so a rendering bug cannot
    crash the dispatch tool
"""


def test_emit_dispatch_event_noop_when_callback_unset(tmp_path, monkeypatch):
    """Default state: on_dispatch_event is None and emit is a silent no-op."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )
    assert sess.on_dispatch_event is None
    # Must not raise
    sess.emit_dispatch_event("ad", "tool_call", "nmap_scan {...}")


def test_emit_dispatch_event_invokes_callback(tmp_path, monkeypatch):
    """When the callback is set, every emit forwards (specialty, kind, content)."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )

    received: list[tuple[str, str, str]] = []
    sess.on_dispatch_event = lambda specialty, kind, content: received.append(
        (specialty, kind, content)
    )

    sess.emit_dispatch_event("ad", "tool_call", "nmap_scan {target:10.10.10.5}")
    sess.emit_dispatch_event("webpentest", "tool_result", "200 OK")
    sess.emit_dispatch_event("ad", "thinking", "Should I enumerate SMB?")
    sess.emit_dispatch_event("ad", "text", "Found a domain controller.")

    assert received == [
        ("ad", "tool_call", "nmap_scan {target:10.10.10.5}"),
        ("webpentest", "tool_result", "200 OK"),
        ("ad", "thinking", "Should I enumerate SMB?"),
        ("ad", "text", "Found a domain controller."),
    ]


def test_emit_dispatch_event_swallows_callback_exceptions(tmp_path, monkeypatch):
    """A buggy renderer must not crash the dispatch tool."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )

    def boom(specialty: str, kind: str, content: str) -> None:
        raise RuntimeError("rendering bug")

    sess.on_dispatch_event = boom

    # Must not propagate the exception
    sess.emit_dispatch_event("ad", "tool_call", "nmap_scan")


def test_emit_dispatch_event_callback_cleared_between_turns(tmp_path, monkeypatch):
    """Clearing the callback returns to no-op behavior (per-turn lifecycle)."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )

    received: list[tuple[str, str, str]] = []
    sess.on_dispatch_event = lambda specialty, kind, content: received.append(
        (specialty, kind, content)
    )
    sess.emit_dispatch_event("ad", "text", "first turn")

    # TUI clears the callback in the `finally` of _run_agent
    sess.on_dispatch_event = None
    sess.emit_dispatch_event("ad", "text", "should not be captured")

    assert received == [("ad", "text", "first turn")]
