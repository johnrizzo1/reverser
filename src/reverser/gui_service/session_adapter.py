"""GUISession — adapter wrapping AgentSession for the GUI service.

Each AgentEvent from the wrapped session is translated to a JSON-
serializable WS frame and published on the EventBus keyed by session_id.
Schema for frames matches docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md
section 3.3.
"""
import asyncio
from typing import Any

from ..agent_session import AgentSession
from ..backends.base import AgentEvent
from ..profiles import Profile
from .event_bus import EventBus


def _event_to_frame(ev: AgentEvent) -> dict[str, Any] | None:
    """Translate a single AgentEvent into a WS frame.

    Returns None for events we deliberately do not surface to the UI.
    """
    if ev.kind == "text":
        return {"type": "text", "role": "assistant", "delta": ev.content}
    if ev.kind == "thinking":
        return {"type": "thinking", "delta": ev.content, "redacted": False}
    if ev.kind == "tool_call":
        return {"type": "tool_call", "name": ev.tool_name, "args": ev.tool_input}
    if ev.kind == "tool_result":
        return {
            "type": "tool_result",
            "ok": not ev.is_error,
            "preview": (ev.content or "")[:4096],
        }
    if ev.kind == "turn":
        return {"type": "status", "phase": "running", "turns": ev.turns}
    if ev.kind == "result":
        # Surface separately as both a budget update and a terminal status
        # so the UI can react to either.
        return {"type": "status", "phase": "awaiting_input",
                "subtype": ev.subtype, "cost": ev.cost, "turns": ev.turns}
    if ev.kind == "error":
        return {"type": "log", "level": "error", "msg": ev.content}
    return None


class GUISession:
    """One live engagement, wrapping AgentSession + publishing to EventBus."""

    def __init__(
        self,
        *,
        session_id: str,
        target: str,
        profile: Profile,
        backend_name: str,
        model: str | None,
        api_base: str | None,
        budget: float,
        max_turns: int,
        bus: EventBus,
        resume_from=None,
    ) -> None:
        self.session_id = session_id
        self._bus = bus
        self._agent = AgentSession(
            binary_path=target,
            profile=profile,
            budget=budget,
            max_turns=max_turns,
            backend_name=backend_name,
            model=model,
            api_base=api_base,
            resume_from=resume_from,
            session_id=session_id if resume_from is None else None,
        )
        self._send_lock = asyncio.Lock()
        self._sudo_password: str | None = None
        # Track the asyncio.Task running send_message so cancel() can
        # preempt an in-flight long-running tool call. Without this,
        # AgentSession.cancel() only flips a cooperative flag that's
        # checked between events — useless while the backend is awaiting
        # a slow tool.
        self._current_send_task: asyncio.Task | None = None

        # Bridge dispatch_specialist sub-agent events (thinking, tool_call,
        # tool_result, text) to the WebSocket so the renderer can show what
        # each dispatched specialist is doing in real time. The TUI hooks
        # the same callback at tui/app.py for the same purpose.
        self._agent.on_dispatch_event = self._on_dispatch_event

    def _on_dispatch_event(self, specialty: str, kind: str, content: str) -> None:
        """Publish a dispatch sub-agent event to the bus.

        Called synchronously from inside the dispatch tool's async loop, so
        we are already on the event-loop thread and can schedule the publish
        as a task. The publish itself is fire-and-forget — the EventBus
        drops to the oldest queued frame if a subscriber is slow.
        """
        frame = {
            "type": "dispatch",
            "specialty": specialty,
            "phase": kind,
            "content": content,
        }
        try:
            asyncio.create_task(self._bus.publish(self.session_id, frame))
        except RuntimeError:
            # No running loop (shouldn't happen in normal flow) — drop.
            pass

    @property
    def stats(self) -> dict[str, Any]:
        s = self._agent.stats
        return {
            "turns": s.turns,
            "total_cost": s.total_cost,
            "budget": s.budget,
            "max_turns": s.max_turns,
            "target": s.target,
            "profile_key": s.profile_key,
        }

    @property
    def snapshot_state(self) -> str:
        return self._agent._snapshot.state  # active|stopped|completed|abandoned

    def set_sudo(self, password: str) -> None:
        """Store the sudo password in memory only (never persisted)."""
        self._sudo_password = password
        # Bridge to the existing tool layer that reads this from env-style
        # state. The same mechanism the TUI's F4 modal uses.
        import os
        os.environ["REVERSER_SUDO_PASSWORD"] = password

    async def send_message(self, user_text: str) -> None:
        """Send a user message and drain all resulting events to the bus."""
        async with self._send_lock:
            # Record the running task so cancel() can preempt it. Cleared
            # in the finally block whether we exit normally or via
            # CancelledError raised by cancel().
            self._current_send_task = asyncio.current_task()
            try:
                await self._bus.publish(self.session_id, {"type": "status", "phase": "running"})
                async for ev in self._agent.send(user_text):
                    frame = _event_to_frame(ev)
                    if frame is not None:
                        await self._bus.publish(self.session_id, frame)
                    # Emit a budget frame after every result event
                    if ev.kind == "result" and ev.cost is not None:
                        spent = self._agent.stats.total_cost
                        await self._bus.publish(self.session_id, {
                            "type": "budget",
                            "spent": spent,
                            "remaining": max(0.0, self._agent.budget - spent),
                            "turn": self._agent.stats.turns,
                        })
                await self._bus.publish(self.session_id, {
                    "type": "status",
                    "phase": "awaiting_input",
                })
            finally:
                self._current_send_task = None

    async def trigger_skill(self, skill_key: str) -> None:
        """Run a profile skill by key. Equivalent to TUI's F1 picker."""
        skill = next(
            (s for s in self._agent.profile.skills if s.key == skill_key),
            None,
        )
        if skill is None:
            raise KeyError(f"unknown skill: {skill_key!r}")
        await self.send_message(skill.prompt)

    def stop(self) -> None:
        self._agent.stop()

    def mark_completed(self) -> None:
        self._agent.mark_completed()

    def update_budget(self, new_budget: float | None, new_max_turns: int | None) -> None:
        if new_budget is not None:
            self._agent.update_budget(new_budget)
        if new_max_turns is not None:
            self._agent.update_max_turns(new_max_turns)

    def cancel(self) -> None:
        """Abort the in-flight turn, if any.

        Flips the cooperative flag on AgentSession AND cancels the
        asyncio.Task running send_message. The latter is what actually
        preempts a blocked-on-slow-tool await; the cooperative flag
        alone can't interrupt an in-progress await.
        """
        self._agent.cancel()
        task = self._current_send_task
        if task is not None and not task.done():
            task.cancel()

    def close(self) -> None:
        self._agent.close()
