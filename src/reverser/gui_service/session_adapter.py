"""GUISession — adapter wrapping AgentSession for the GUI service.

Each AgentEvent from the wrapped session is translated to a JSON-
serializable WS frame and published on the EventBus keyed by session_id.
Schema for frames matches docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md
section 3.3.
"""
import asyncio
import uuid
from typing import Any, TYPE_CHECKING

from ..agent_session import AgentSession
from ..backends.base import AgentEvent
from ..profiles import Profile
from .event_bus import EventBus

if TYPE_CHECKING:
    from ..targets import Target


def _event_to_frame(ev: AgentEvent) -> dict[str, Any] | None:
    """Translate a single AgentEvent into a WS frame."""
    if ev.kind == "text":
        return {"type": "text", "role": "assistant", "delta": ev.content, "turn": ev.turn}
    if ev.kind == "thinking":
        return {"type": "thinking", "delta": ev.content, "redacted": False, "turn": ev.turn}
    if ev.kind == "llm_status":
        frame: dict[str, Any] = {
            "type": "llm_status",
            "phase": ev.phase or ev.subtype,
            "detail": ev.content,
            "turn": ev.turn,
        }
        if ev.elapsed_ms is not None:
            frame["elapsed_ms"] = ev.elapsed_ms
        if ev.first_token_ms is not None:
            frame["first_token_ms"] = ev.first_token_ms
        if ev.generated_chars is not None:
            frame["generated_chars"] = ev.generated_chars
        if ev.rate_chars_per_sec is not None:
            frame["rate_chars_per_sec"] = ev.rate_chars_per_sec
        return frame
    if ev.kind == "tool_call":
        return {
            "type": "tool_call",
            "name": ev.tool_name,
            "args": ev.tool_input,
            "tool_use_id": ev.tool_use_id,
            "turn": ev.turn,
        }
    if ev.kind == "tool_result":
        return {
            "type": "tool_result",
            "ok": not ev.is_error,
            "preview": (ev.content or "")[:4096],
            "tool_use_id": ev.tool_use_id,
            "turn": ev.turn,
        }
    if ev.kind == "turn":
        return {"type": "status", "phase": "running", "turns": ev.turns}
    if ev.kind == "result":
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
        target_obj: "Target | None" = None,
    ) -> None:
        self.session_id = session_id
        self._bus = bus
        agent_kwargs = {
            "profile": profile,
            "budget": budget,
            "max_turns": max_turns,
            "backend_name": backend_name,
            "model": model,
            "api_base": api_base,
        }
        if resume_from is None and target_obj is not None:
            self._agent = AgentSession.from_target(
                target_obj,
                **agent_kwargs,
                session_id=session_id,
            )
        else:
            self._agent = AgentSession(
                binary_path=target,
                **agent_kwargs,
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
        self._pending_messages: list[dict[str, str]] = []

        # Bridge dispatch_specialist sub-agent events (thinking, tool_call,
        # tool_result, text) to the WebSocket so the renderer can show what
        # each dispatched specialist is doing in real time. The TUI hooks
        # the same callback at tui/app.py for the same purpose.
        self._agent.on_dispatch_event = self._on_dispatch_event
        self._agent.on_kb_event = self._on_kb_event
        self._agent.has_pending_user_messages = self._has_pending_messages

    def _has_pending_messages(self) -> bool:
        return bool(self._pending_messages)

    async def queue_message(self, text: str) -> dict[str, str]:
        """Queue analyst input to be consumed after the current run finishes."""
        msg = {"id": uuid.uuid4().hex, "text": text}
        self._pending_messages.append(msg)
        await self._bus.publish(self.session_id, {
            "type": "pending_message",
            "action": "create",
            "message": msg,
        })
        return msg

    async def delete_pending_message(self, message_id: str) -> bool:
        """Delete an unconsumed queued message."""
        for i, msg in enumerate(self._pending_messages):
            if msg["id"] == message_id:
                self._pending_messages.pop(i)
                await self._bus.publish(self.session_id, {
                    "type": "pending_message",
                    "action": "delete",
                    "id": message_id,
                })
                return True
        return False

    def _consume_pending_messages(self) -> list[dict[str, str]]:
        messages = self._pending_messages
        self._pending_messages = []
        return messages

    async def _publish_consumed_pending_messages(self, messages: list[dict[str, str]]) -> None:
        for msg in messages:
            await self._bus.publish(self.session_id, {
                "type": "pending_message",
                "action": "consumed",
                "id": msg["id"],
            })

    @staticmethod
    def _pending_messages_prompt(messages: list[dict[str, str]]) -> str:
        lines = [
            "The analyst sent these updates while you were working.",
            "Before continuing, revise your plan and next action based on them:",
            "",
        ]
        for i, msg in enumerate(messages, start=1):
            lines.append(f"{i}. {msg['text']}")
        return "\n".join(lines)

    async def _run_agent_message(self, user_text: str) -> None:
        from ..sessions import current_session
        self._current_send_task = asyncio.current_task()
        session_token = current_session.set(self._agent)
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
            current_session.reset(session_token)
            self._current_send_task = None

    def _on_dispatch_event(
        self,
        specialty: str,
        dispatch_id: str,
        sub_turn: int,
        kind: str,
        content: str,
    ) -> None:
        """Publish a dispatch sub-agent event to the bus.

        kind is one of: "start", "end", "text", "thinking", "tool_call",
        "tool_result", "tool_error". For "start"/"end", `content` is a JSON
        string of structured payload; for other kinds it's free-form text.
        """
        frame: dict[str, Any] = {
            "type": "dispatch",
            "dispatch_id": dispatch_id,
            "turn": max(1, self._agent.stats.turns),
            "phase": kind,
            "specialty": specialty,
        }
        if kind in ("start", "end"):
            try:
                import json
                payload = json.loads(content) if content else {}
                frame.update(payload)
            except Exception:
                pass
        else:
            frame["sub_turn"] = sub_turn
            frame["content"] = content

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._bus.publish(self.session_id, frame))

    def _on_kb_event(self, kind: str, payload: dict) -> None:
        """Publish a KB write to the bus.

        kind is "hypothesis" or "finding"; payload is {"action", "row"}.
        """
        frame = {"type": kind, **payload}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._bus.publish(self.session_id, frame))

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
        """Store the sudo password in memory only (never persisted).

        Mirrors what the TUI's F4 modal does (tui/app.py): writes the
        password into the module-level `_sudo_password` in tools/_common.py
        so the network tools can read it via `get_sudo_password()`. Also
        keeps a copy on the GUISession instance and in the env var for any
        subprocess that inherits it. Without the `set_sudo_password()`
        call below, the GUI's Save button silently set the env var only,
        and nmap/netexec privileged scans still ran without credentials.
        """
        self._sudo_password = password
        from ..tools._common import set_sudo_password
        set_sudo_password(password)
        import os
        os.environ["REVERSER_SUDO_PASSWORD"] = password

    async def send_message(self, user_text: str) -> None:
        """Send a user message and drain all resulting events to the bus."""
        async with self._send_lock:
            await self._run_agent_message(user_text)

            while self._pending_messages:
                pending = self._consume_pending_messages()
                await self._publish_consumed_pending_messages(pending)
                await self._run_agent_message(self._pending_messages_prompt(pending))

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
