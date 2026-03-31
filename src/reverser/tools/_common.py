"""Shared infrastructure for RE tools: subprocess execution, output truncation, pagination."""

import os
import subprocess

DEFAULT_MAX_OUTPUT = 8000  # characters (~2k tokens)
DEFAULT_TIMEOUT = 30  # seconds

# PE magic bytes: MZ header
_PE_MAGIC = b"MZ"


def is_pe(path: str) -> bool:
    """Detect if a file is a Windows PE executable (MZ header)."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == _PE_MAGIC
    except (OSError, IOError):
        return False


def wine_wrap(cmd: list[str], binary_path: str) -> list[str]:
    """If binary_path is a PE, prepend 'wine' to the command."""
    if is_pe(binary_path):
        return ["wine"] + cmd
    return cmd


def run_cmd(
    cmd: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    cwd: str | None = None,
    stdin_data: str | None = None,
) -> dict:
    """Run a subprocess and return captured output, truncating if needed.

    Returns dict with keys: stdout, stderr, returncode, truncated.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            input=stdin_data,
        )
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s: {' '.join(cmd)}",
            "returncode": -1,
            "truncated": False,
        }
    except FileNotFoundError:
        return {
            "stdout": "",
            "stderr": f"Command not found: {cmd[0]}",
            "returncode": -1,
            "truncated": False,
        }

    truncated = False
    stdout = result.stdout
    if len(stdout) > max_output:
        stdout = stdout[:max_output]
        stdout += "\n\n[OUTPUT TRUNCATED — use offset/limit parameters for pagination]"
        truncated = True

    return {
        "stdout": stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "truncated": truncated,
    }


def paginate(text: str, offset: int = 0, limit: int = 50) -> dict:
    """Paginate text by lines.

    Returns dict with keys: content, total_lines, showing.
    """
    lines = text.split("\n")
    total = len(lines)
    selected = lines[offset : offset + limit]
    end = min(offset + limit, total)

    return {
        "content": "\n".join(selected),
        "total_lines": total,
        "showing": f"lines {offset + 1}-{end} of {total}",
    }


def format_tool_result(text: str) -> dict:
    """Wrap text in MCP tool result content block format."""
    return {"content": [{"type": "text", "text": text}]}


def format_error(text: str) -> dict:
    """Wrap error text in MCP tool result format with is_error flag."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def cmd_result_to_tool_result(result: dict) -> dict:
    """Convert a run_cmd result dict to an MCP tool result."""
    if result["returncode"] != 0 and not result["stdout"]:
        return format_error(result["stderr"] or f"Command failed with exit code {result['returncode']}")

    output = result["stdout"]
    if result["stderr"]:
        output += f"\n\n[stderr]: {result['stderr'][:500]}"
    return format_tool_result(output)
