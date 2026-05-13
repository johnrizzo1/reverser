"""Backward-compat shim. AgentSession moved to reverser.agent_session.

Import directly from the new location instead:
    from reverser.agent_session import AgentSession
"""

from ..agent_session import (  # noqa: F401
    AgentSession,
    Exchange,
    TurnStats,
)
