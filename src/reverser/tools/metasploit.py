"""Metasploit bridge tools: searchsploit, msfvenom, and msfrpcd RPC.

All 8 tools live in this file (per spec D2). Daemon lifecycle is shared:
one msfrpcd at 127.0.0.1:55553, per-target MSF workspaces.

See docs/superpowers/specs/2026-05-11-metasploit-bridge-design.md for the
full design including the 12 architectural decisions (D1-D12).
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any


# ── Constants ───────────────────────────────────────────────────────

DEFAULT_RPC_HOST = "127.0.0.1"
DEFAULT_RPC_PORT = 55553
DEFAULT_RPC_USER = "msf"
_AUTH_PASSWORD_BYTES = 32   # secrets.token_urlsafe(32) → ~43 char string
_RPC_READY_TIMEOUT_DEFAULT = 60


# ── Path helpers ────────────────────────────────────────────────────

def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def _msf_state_dir() -> Path:
    """Shared msfrpc state directory: <targets_root>/.shared/msfrpc/.

    Created on first access with mode 0700 (best-effort; some filesystems
    do not honor chmod).
    """
    p = _targets_root() / ".shared" / "msfrpc"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _auth_path() -> Path:
    return _msf_state_dir() / "auth.json"


def _pidfile_path() -> Path:
    return _msf_state_dir() / "pidfile"


def _lock_path() -> Path:
    return _msf_state_dir() / "auth.json.lock"


# ── Auth file (D8: random password, 0600, persistent) ───────────────

def _read_or_create_auth() -> dict:
    """Return the shared auth dict.

    If auth.json exists, parse and return. Otherwise generate a random
    32-char password and write 0600. Persistent across reverser processes
    so already-running daemons can be authenticated.
    """
    path = _auth_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass  # fall through and regenerate

    auth = {
        "user": DEFAULT_RPC_USER,
        "password": secrets.token_urlsafe(_AUTH_PASSWORD_BYTES),
        "host": DEFAULT_RPC_HOST,
        "port": DEFAULT_RPC_PORT,
        "ssl": False,
    }
    path.write_text(json.dumps(auth, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return auth


# ── Tools list (populated as tools are added in subsequent tasks) ──

TOOLS: list = []


# ── searchsploit (exploit-db local search) ──────────────────────────

import subprocess

from claude_agent_sdk import tool

from ..kb import for_target, require_pentest_auth, AuthorizationError
from ._common import format_tool_result, format_error


def _run_searchsploit(query: str, *, cve_only: bool, title_only: bool) -> dict:
    """Invoke `searchsploit -j`. Pulled out for test mocking."""
    cmd = ["searchsploit", "-j"]
    if cve_only:
        cmd.append("--cve")
    if title_only:
        cmd.append("-t")
    cmd.append(query)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "searchsploit timed out after 30s",
                "returncode": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "searchsploit not found in PATH "
                "(install via `exploitdb` package)", "returncode": 127}


def _parse_searchsploit_json(raw: str, *, limit: int) -> tuple[list[dict], int]:
    """Parse `searchsploit -j` output. Returns (candidates, total_count).

    total_count is the pre-truncation count; candidates is truncated to `limit`.
    Each candidate dict has: exploit_id, title, type, platform, date, path, cve.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], 0
    raw_results = data.get("RESULTS_EXPLOIT", []) or []
    total = len(raw_results)
    out = []
    for entry in raw_results[:limit]:
        out.append({
            "exploit_id": entry.get("EDB-ID", ""),
            "title": entry.get("Title", ""),
            "type": entry.get("Type", ""),
            "platform": entry.get("Platform", ""),
            "date": entry.get("Date_Published", ""),
            "path": entry.get("Path", ""),
            "cve": entry.get("Codes", "") or "",
        })
    return out, total


@tool(
    "searchsploit_search",
    "Search the local exploit-db (via searchsploit) for a CVE, keyword, or "
    "software name. Returns a ranked candidate list with EDB-IDs, titles, "
    "platforms, and paths. If `target` is given, records a KB note "
    "summarizing the candidates.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "CVE (e.g. 'CVE-2022-12345') or keyword "
                                     "(e.g. 'ProFTPD')"},
            "cve_only": {"type": "boolean", "default": False,
                         "description": "Only return CVE-tagged results (--cve)"},
            "title_only": {"type": "boolean", "default": True,
                           "description": "Match against title only (saner default; --title)"},
            "target": {"type": "string",
                       "description": "Optional target — when set, the candidate "
                                      "list is recorded as a KB note."},
            "limit": {"type": "integer", "default": 30,
                      "description": "Max candidates returned (default 30)"},
        },
        "required": ["query"],
    },
)
async def searchsploit_search(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    query = args["query"]
    cve_only = bool(args.get("cve_only", False))
    title_only = bool(args.get("title_only", True))
    target = args.get("target") or None
    limit = int(args.get("limit", 30))

    proc = _run_searchsploit(query, cve_only=cve_only, title_only=title_only)
    if proc["returncode"] != 0:
        return format_error(
            f"searchsploit failed (rc={proc['returncode']}): "
            f"{proc['stderr'][:500] or proc['stdout'][:500]}"
        )

    candidates, total = _parse_searchsploit_json(proc["stdout"], limit=limit)

    if not candidates:
        return format_tool_result(f"searchsploit: no results for {query!r}.")

    lines = [f"searchsploit results for {query!r} "
             f"(showing {len(candidates)} of {total}):", ""]
    for c in candidates:
        cve = f" [{c['cve']}]" if c["cve"] else ""
        lines.append(f"  EDB-{c['exploit_id']}{cve}")
        lines.append(f"    {c['title']}")
        lines.append(f"    {c['type']}/{c['platform']}  ({c['date']})")
        lines.append(f"    path: {c['path']}")
        lines.append("")

    summary = "\n".join(lines)

    if target:
        try:
            kb = for_target(target)
            note_lines = [
                f"searchsploit query: {query} "
                f"(cve_only={cve_only}, title_only={title_only})",
                f"  matches: {total} total, {len(candidates)} returned",
            ]
            for c in candidates[:10]:
                cve = f" [{c['cve']}]" if c["cve"] else ""
                note_lines.append(f"    EDB-{c['exploit_id']}{cve} — {c['title']}")
            kb.record_note("\n".join(note_lines))
        except Exception:
            pass  # best-effort KB write

    return format_tool_result(summary)


TOOLS.append(searchsploit_search)


# ── msfvenom (payload generator) ────────────────────────────────────

import hashlib
import re as _re

from ..kb import ArtifactFact


_PAYLOAD_NAME_RE = _re.compile(r"[^a-zA-Z0-9_-]+")


def _mangle_payload_name(payload: str) -> str:
    """Turn 'windows/x64/meterpreter/reverse_tcp' into a filesystem-safe stem."""
    return _PAYLOAD_NAME_RE.sub("_", payload).strip("_")


def _payload_loot_dir(target: str) -> Path:
    """Per-target loot/payloads/ directory."""
    return _targets_root() / target / "loot" / "payloads"


def _run_msfvenom(cmd: list[str], timeout: int = 120) -> dict:
    """Invoke msfvenom. Pulled out for test mocking."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"msfvenom timed out after {timeout}s",
                "returncode": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "msfvenom not found in PATH "
                "(install via `metasploit-framework` package)", "returncode": 127}


@tool(
    "msfvenom_generate",
    "Generate a Metasploit payload via msfvenom. Writes the binary to "
    "targets/<target>/loot/payloads/<name>-<sha8>.<ext> and records an "
    "ArtifactFact in the KB. Common payloads: windows/x64/meterpreter/reverse_tcp, "
    "linux/x64/shell_reverse_tcp.",
    {
        "type": "object",
        "properties": {
            "payload": {"type": "string",
                        "description": "MSF payload name (e.g. windows/x64/meterpreter/reverse_tcp)"},
            "lhost": {"type": "string", "description": "Listener host"},
            "lport": {"type": "integer", "default": 4444,
                      "description": "Listener port"},
            "format": {"type": "string", "default": "exe",
                       "description": "Output format (exe, elf, raw, python, ...)"},
            "target": {"type": "string",
                       "description": "Target identifier — determines loot dir and KB"},
            "encoder": {"type": "string", "default": "",
                        "description": "Optional encoder (e.g. x64/shikata_ga_nai)"},
            "iterations": {"type": "integer", "default": 1,
                           "description": "Encoder iterations (only if encoder set)"},
            "bad_chars": {"type": "string", "default": "",
                          "description": "Bytes to avoid (e.g. '\\x00\\x0a\\x0d')"},
            "options": {"type": "object", "default": {},
                        "description": "Extra payload options as KEY=VALUE map"},
        },
        "required": ["payload", "lhost", "target"],
    },
)
async def msfvenom_generate(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    payload = args["payload"]
    lhost = args["lhost"]
    lport = int(args.get("lport", 4444))
    fmt = args.get("format", "exe") or "exe"
    target = args["target"]
    encoder = (args.get("encoder") or "").strip()
    iterations = int(args.get("iterations", 1) or 1)
    bad_chars = args.get("bad_chars") or ""
    options = args.get("options") or {}

    loot_dir = _payload_loot_dir(target)
    loot_dir.mkdir(parents=True, exist_ok=True)
    stem = _mangle_payload_name(payload)
    # We don't know the sha8 until after msfvenom runs; write to a temp name
    # then rename. Use a fixed temp-stem with the process PID so concurrent
    # invocations don't collide.
    tmp_path = loot_dir / f"{stem}-tmp-{os.getpid()}.{fmt}"

    cmd: list[str] = ["msfvenom", "-p", payload,
                      f"LHOST={lhost}", f"LPORT={lport}",
                      "-f", fmt, "-o", str(tmp_path)]
    if encoder:
        cmd += ["-e", encoder, "-i", str(iterations)]
    if bad_chars:
        cmd += ["-b", bad_chars]
    for k, v in options.items():
        cmd.append(f"{k}={v}")

    proc = _run_msfvenom(cmd)
    if proc["returncode"] != 0 or not tmp_path.is_file():
        # Clean up the temp file if it was created
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return format_error(
            f"msfvenom failed (rc={proc['returncode']}): "
            f"{proc['stderr'][:1000] or proc['stdout'][:1000]}"
        )

    data = tmp_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sha8 = sha[:8]
    final_path = loot_dir / f"{stem}-{sha8}.{fmt}"
    tmp_path.rename(final_path)

    try:
        kb = for_target(target)
        kb.record_artifact(ArtifactFact(
            kind="payload",
            path=str(final_path),
            sha256=sha,
            source_tool="msfvenom",
        ))
    except Exception:
        pass  # best-effort

    summary = (
        f"msfvenom payload generated:\n"
        f"  path:    {final_path}\n"
        f"  payload: {payload}\n"
        f"  size:    {len(data)} bytes\n"
        f"  sha256:  {sha}"
    )
    return format_tool_result(summary)


TOOLS.append(msfvenom_generate)
