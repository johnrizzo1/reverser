"""Shared infrastructure for RE tools: subprocess execution, output truncation, pagination."""

import os
import subprocess
import zipfile

DEFAULT_MAX_OUTPUT = 8000  # characters (~2k tokens)
DEFAULT_TIMEOUT = 30  # seconds
WEB_TOOL_TIMEOUT = 120  # seconds — web scanners can be slow


def is_url(target: str) -> bool:
    """Check if a target string looks like a URL or domain (not a file path)."""
    if not target:
        return False
    if target.startswith(("http://", "https://")):
        return True
    # Looks like a domain: contains a dot, doesn't start with / or .
    if target[0] in ("/", "."):
        return False
    # Must have a dot-separated domain-like structure (e.g. example.com)
    parts = target.split(".", 1)
    return len(parts) == 2 and len(parts[0]) > 0 and len(parts[1]) > 0


def maybe_extract_archive(path: str) -> tuple[str, list[str]]:
    """If *path* is a zip archive, extract it and return (extract_dir, file_list).

    The archive is extracted into a sibling directory named after the zip
    (without extension).  If *path* is not a zip file the function returns
    ``("", [])``.

    Returns:
        (extract_dir, files)  – *extract_dir* is the top-level directory,
        *files* is a list of extracted member paths (absolute).
    """
    if not zipfile.is_zipfile(path):
        return ("", [])

    base = os.path.splitext(os.path.basename(path))[0]
    extract_dir = os.path.join(os.path.dirname(path), base)
    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(extract_dir)
        members = [os.path.join(extract_dir, m) for m in zf.namelist()
                    if not m.endswith("/")]

    return (extract_dir, members)


def check_web_authorized() -> dict | None:
    """Return an error dict if web pentest is not authorized, None otherwise.

    Authorization requires REVERSER_PENTEST_AUTHORIZED=1 env var
    or a .reverser-authorized file in the working directory.
    """
    if os.environ.get("REVERSER_PENTEST_AUTHORIZED") == "1":
        return None
    if os.path.exists(".reverser-authorized"):
        return None
    return format_error(
        "Web pentest tools require explicit authorization.\n"
        "Set REVERSER_PENTEST_AUTHORIZED=1 or create .reverser-authorized in the working directory.\n"
        "This confirms you have written authorization to test the target."
    )

# PE magic bytes: MZ header
_PE_MAGIC = b"MZ"

# ── Sudo password store ─────────────────────────────────────────────
_sudo_password: str | None = None


def set_sudo_password(password: str | None) -> None:
    """Store the sudo password for use by tools that need root."""
    global _sudo_password
    _sudo_password = password


def get_sudo_password() -> str | None:
    """Retrieve the stored sudo password."""
    return _sudo_password


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
