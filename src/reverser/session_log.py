"""Session logger — records all agent activity to a JSONL file for writeup generation."""

import json
import os
from datetime import datetime, timezone


class SessionLog:
    """Append-only JSONL logger that captures the full agent session."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "w")

    def _write(self, entry: dict):
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        self._f.write(json.dumps(entry, default=str) + "\n")
        self._f.flush()

    def log_session_start(self, binary_path: str, mode: str, budget: float):
        self._write({
            "type": "session_start",
            "binary": binary_path,
            "mode": mode,
            "budget": budget,
        })

    def log_turn(self, turn: int):
        self._write({"type": "turn", "turn": turn})

    def log_thinking(self, thinking: str):
        self._write({"type": "thinking", "text": thinking})

    def log_text(self, text: str):
        self._write({"type": "text", "text": text})

    def log_tool_call(self, name: str, input: dict):
        self._write({"type": "tool_call", "name": name, "input": input})

    def log_tool_result(self, content: str, is_error: bool = False):
        self._write({"type": "tool_result", "content": content, "is_error": is_error})

    def log_session_end(self, result: str | None, cost: float | None, turns: int | None, subtype: str = "success"):
        self._write({
            "type": "session_end",
            "subtype": subtype,
            "result": result,
            "cost": cost,
            "turns": turns,
        })

    def log_session_resumed(self, session_id: str, prior_turn: int, prior_cost: float):
        self._write({
            "type": "session_resumed",
            "session_id": session_id,
            "prior_turn": prior_turn,
            "prior_cost": prior_cost,
        })

    def log_session_stopped(self, cost: float, turns: int):
        self._write({
            "type": "session_stopped",
            "cost": cost,
            "turns": turns,
        })

    def log_session_completed(self, cost: float, turns: int):
        self._write({
            "type": "session_completed",
            "cost": cost,
            "turns": turns,
        })

    def log_dispatch_event(self, specialty: str, kind: str, content: str):
        """Persist a dispatch_specialist sub-agent event so read-only
        session replay can render it.

        Specialty: 'ad', 'webpentest', etc.
        Kind: 'text' | 'thinking' | 'tool_call' | 'tool_result' | 'tool_error' | 'start' | 'result' | 'error'.
        Content: truncated to 4096 chars.
        """
        self._write({
            "type": "dispatch",
            "specialty": specialty,
            "kind": kind,
            "content": (content or "")[:4096],
        })

    def close(self):
        self._f.close()


def load_session_log(path: str) -> list[dict]:
    """Read a JSONL session log and return list of entries."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def session_log_path(binary_path: str, log_dir: str | None = None, is_url: bool = False) -> str:
    """Generate a default log file path based on binary/target name and timestamp."""
    if is_url and binary_path:
        # Extract domain from URL for the filename
        from urllib.parse import urlparse
        parsed = urlparse(binary_path if "://" in binary_path else f"https://{binary_path}")
        binary_name = (parsed.hostname or "target").replace(".", "_")
    else:
        binary_name = os.path.splitext(os.path.basename(binary_path))[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{binary_name}_{ts}.jsonl"

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, filename)

    # Default: logs/ directory in project root
    default_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(default_dir, exist_ok=True)
    return os.path.join(default_dir, filename)
