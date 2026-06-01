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


def test_triage_help_mentions_model_family():
    """--model-family appears in the triage-command help output."""
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "triage", "--help"],
        capture_output=True, text=True,
    )
    assert "--model-family" in result.stdout, result.stdout
    assert "deepseek" in result.stdout, result.stdout


def test_interactive_help_mentions_model_family():
    """And in the interactive-command help too (same shared helper)."""
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "--model-family" in result.stdout, result.stdout


def test_interactive_help_mentions_validation_flags():
    """--validation-backend / --validation-model / --validation-api-base appear in interactive help."""
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "--validation-backend" in result.stdout, result.stdout
    assert "--validation-model" in result.stdout, result.stdout
    assert "--validation-api-base" in result.stdout, result.stdout


def test_interactive_validation_flags_parse_without_error():
    """Passing --validation-backend + --validation-model to --list-profiles exits cleanly."""
    result = subprocess.run(
        [
            PYTHON, "-m", "reverser", "interactive",
            "--list-profiles",
            "--validation-backend", "claude",
            "--validation-model", "advm",
        ],
        capture_output=True, text=True,
    )
    # --list-profiles prints profiles and exits 0; unrecognized arg would exit non-zero
    assert result.returncode == 0, result.stderr
    assert "general" in result.stdout.lower(), result.stdout
