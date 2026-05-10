"""CLI smoke tests."""

import subprocess

PYTHON = "/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python"


def test_interactive_help_mentions_max_parallel():
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "--max-parallel" in result.stdout, result.stdout


def test_interactive_help_mentions_manager_profile():
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "manager" in result.stdout.lower(), result.stdout


def test_list_profiles_includes_manager():
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--list-profiles"],
        capture_output=True, text=True,
    )
    assert "manager" in result.stdout.lower(), result.stdout
