"""Verify AgentSession is importable from the canonical (non-TUI) location."""


def test_agent_session_importable_from_canonical_path():
    from reverser.agent_session import AgentSession, Exchange, TurnStats
    assert AgentSession is not None
    assert Exchange is not None
    assert TurnStats is not None


def test_tui_session_still_works_for_backwards_compat():
    """Existing TUI imports must continue to function during the transition."""
    from reverser.tui.session import AgentSession as TUIAgentSession
    from reverser.agent_session import AgentSession as CanonicalAgentSession
    assert TUIAgentSession is CanonicalAgentSession
