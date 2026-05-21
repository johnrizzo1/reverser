"""SessionManager — owns the active GUISession + enumerates historical sessions.

Phase 1 constraint: at most one active session at a time. Creating a new
session implicitly stops the previous active one (snapshot is preserved
for later resume).
"""
import os
import secrets
import time
from pathlib import Path
from typing import Any

from ..profiles import get_profile
from ..sessions import list_all as list_all_snapshots
from .event_bus import EventBus
from .session_adapter import GUISession


_NETWORK_PROFILES = {
    "pentest", "webpentest", "webapi", "webrecon", "ad", "manager", "exploit",
}


def _require_pentest_auth(profile_key: str) -> None:
    """Mirrors the existing CLI/TUI authorization gate.

    Raises PermissionError if the profile touches the network but the user
    hasn't acknowledged authorization via env var or marker file.
    """
    if profile_key not in _NETWORK_PROFILES:
        return
    if os.environ.get("REVERSER_PENTEST_AUTHORIZED") == "1":
        return
    if Path(".reverser-authorized").is_file():
        return
    raise PermissionError(
        "network-touching profiles require REVERSER_PENTEST_AUTHORIZED=1 "
        "or a .reverser-authorized marker file in the project root"
    )


class SessionManager:
    def __init__(self, *, bus: EventBus, targets_root: Path | str = "targets") -> None:
        self._bus = bus
        self._targets_root = Path(targets_root)
        self.active: GUISession | None = None

    @staticmethod
    def _mint_session_id() -> str:
        # Match the existing sessions.py naming scheme: ISO-ish + suffix
        return f"{time.strftime('%Y-%m-%dT%H-%M-%S')}-{secrets.token_hex(3)}"

    def _with_targets_root(self) -> dict[str, str]:
        """Return env-override dict so sessions.py uses our targets_root."""
        return {"REVERSER_TARGETS_DIR": str(self._targets_root)}

    async def create_session(
        self,
        *,
        target: str,
        profile_key: str,
        backend_name: str,
        model: str | None,
        api_base: str | None,
        budget: float,
        max_turns: int,
    ) -> dict[str, Any]:
        _require_pentest_auth(profile_key)

        # If there is an active session, stop it first.
        if self.active is not None:
            self.active.stop()
            self.active.close()
            self.active = None

        profile = get_profile(profile_key)
        session_id = self._mint_session_id()

        # Point sessions.py at our targets_root for snapshot writes
        old_targets_dir = os.environ.get("REVERSER_TARGETS_DIR")
        os.environ["REVERSER_TARGETS_DIR"] = str(self._targets_root)
        try:
            gs = GUISession(
                session_id=session_id,
                target=target,
                profile=profile,
                backend_name=backend_name,
                model=model,
                api_base=api_base,
                budget=budget,
                max_turns=max_turns,
                bus=self._bus,
            )
        finally:
            if old_targets_dir is None:
                os.environ.pop("REVERSER_TARGETS_DIR", None)
            else:
                os.environ["REVERSER_TARGETS_DIR"] = old_targets_dir

        self.active = gs
        return self._serialize(gs)

    async def resume_session(
        self,
        *,
        snapshot_id: str,
        target: str,
        backend_name: str | None,
        model: str | None,
        api_base: str | None,
    ) -> dict[str, Any]:
        from ..sessions import load as load_snapshot
        snap = load_snapshot(target, snapshot_id)
        _require_pentest_auth(snap.config.profile)

        if self.active is not None:
            self.active.stop()
            self.active.close()
            self.active = None

        profile = get_profile(snap.config.profile)

        old_targets_dir = os.environ.get("REVERSER_TARGETS_DIR")
        os.environ["REVERSER_TARGETS_DIR"] = str(self._targets_root)
        try:
            gs = GUISession(
                session_id=snap.session_id,
                target=snap.target,
                profile=profile,
                backend_name=backend_name or snap.config.backend,
                model=model if model is not None else snap.config.model,
                api_base=api_base if api_base is not None else snap.config.api_base,
                budget=snap.config.budget,
                max_turns=snap.config.max_turns,
                bus=self._bus,
                resume_from=snap,
            )
        finally:
            if old_targets_dir is None:
                os.environ.pop("REVERSER_TARGETS_DIR", None)
            else:
                os.environ["REVERSER_TARGETS_DIR"] = old_targets_dir

        self.active = gs
        return self._serialize(gs)

    def get_active(self, session_id: str) -> GUISession:
        if self.active is None or self.active.session_id != session_id:
            raise KeyError(session_id)
        return self.active

    def list_sessions(self) -> list[dict[str, Any]]:
        # Historical sessions from disk — point list_all at our targets_root
        old_targets_dir = os.environ.get("REVERSER_TARGETS_DIR")
        os.environ["REVERSER_TARGETS_DIR"] = str(self._targets_root)
        try:
            snapshots = list_all_snapshots()
        finally:
            if old_targets_dir is None:
                os.environ.pop("REVERSER_TARGETS_DIR", None)
            else:
                os.environ["REVERSER_TARGETS_DIR"] = old_targets_dir

        out = []
        for s in snapshots:
            out.append({
                "id": s.session_id,
                "target": s.target,
                "profile": s.config.profile,
                "state": s.state,
                "turns": s.stats.turns,
                "total_cost": s.stats.total_cost,
                "stopped_at": s.stopped_at,
                "archived_at": s.archived_at,
                "backend": s.config.backend,
                "model": s.config.model,
                "api_base": s.config.api_base,
                "budget": s.config.budget,
                "max_turns": s.config.max_turns,
            })
        # The active session overrides whatever state-on-disk has
        if self.active is not None:
            for row in out:
                if row["id"] == self.active.session_id:
                    serialized = self._serialize(self.active)
                    row.update({
                        "state": "active",
                        "turns": serialized["turns"],
                        "total_cost": serialized["total_cost"],
                        "budget": serialized["budget"],
                        "max_turns": serialized["max_turns"],
                    })
                    break
            else:
                out.append(self._serialize(self.active))
        return out

    @staticmethod
    def _serialize(gs: GUISession) -> dict[str, Any]:
        s = gs.stats
        cfg = gs._agent._snapshot.config
        return {
            "id": gs.session_id,
            "state": "active",
            "target": s["target"],
            "profile": s["profile_key"],
            "turns": s["turns"],
            "total_cost": s["total_cost"],
            "budget": s["budget"],
            "max_turns": s["max_turns"],
            "archived_at": None,
            "backend": cfg.backend,
            "model": cfg.model,
            "api_base": cfg.api_base,
        }
