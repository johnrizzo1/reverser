"""Tests for the --list-sessions and --resume CLI surface."""

import os
import subprocess

PYTHON = "/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python"


def _run_cli(args, env_overrides=None):
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [PYTHON, "-m", "reverser", *args],
        capture_output=True, text=True, env=env,
    )


def test_list_sessions_with_no_sessions_says_empty(tmp_path):
    """--list-sessions on an empty targets dir says 'no sessions'."""
    result = _run_cli(
        ["--list-sessions"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "no session" in result.stdout.lower()


def test_list_sessions_shows_existing_sessions(tmp_path):
    """--list-sessions shows the session table when sessions exist."""
    sessions_dir = tmp_path / "10.10.10.5" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "2026-05-09T14-23-00.json").write_text("""\
{
  "session_id": "2026-05-09T14-23-00",
  "target": "10.10.10.5",
  "log_path": "logs/test.jsonl",
  "state": "stopped",
  "started_at": "2026-05-09T14:23:00",
  "last_active_at": "2026-05-09T18:47:00",
  "config": {"profile": "manager", "budget": 5.0, "max_turns": 50, "max_parallel": 1, "backend": "claude", "model": null, "api_base": null},
  "stats": {"total_cost": 1.84, "turns": 47},
  "conversation": [],
  "ui": {"focused_panel": "chat", "chat_scroll_position": 0, "last_skill_key": null, "input_buffer": ""},
  "schema_version": 1
}
""")

    result = _run_cli(
        ["--list-sessions"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "2026-05-09T14-23-00" in result.stdout
    assert "10.10.10.5" in result.stdout
    assert "manager" in result.stdout
    assert "stopped" in result.stdout
    assert "1.84" in result.stdout
