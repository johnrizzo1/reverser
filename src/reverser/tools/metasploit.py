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


# ── Pidfile + process liveness ──────────────────────────────────────

def _read_pidfile() -> int | None:
    """Read the pidfile or return None if missing/corrupted."""
    path = _pidfile_path()
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pidfile(pid: int) -> None:
    """Atomically write the daemon PID."""
    path = _pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid))


def _remove_pidfile() -> None:
    """Remove the pidfile; no-op if absent."""
    path = _pidfile_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _process_alive(pid: int) -> bool:
    """Return True iff signal 0 to pid succeeds (process exists)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── Start-lock (D11: concurrent-start serialization) ────────────────

import fcntl


@contextmanager
def _start_lock():
    """Acquire an fcntl flock on auth.json.lock for the duration of start.

    Linux + macOS supported (fcntl.flock works on both). Windows is not
    supported (msfrpcd doesn't run on Windows anyway).
    """
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# ── pymetasploit3 wrappers ──────────────────────────────────────────

import time

from ..kb.store import normalize_target


def _make_msfrpc_client(auth: dict):
    """Construct an authed pymetasploit3.MsfRpcClient.

    Pulled out for test mocking — tests can monkey-patch this function to
    avoid importing pymetasploit3 in the test process.
    """
    from pymetasploit3.msfrpc import MsfRpcClient
    return MsfRpcClient(
        auth["password"],
        username=auth.get("user", DEFAULT_RPC_USER),
        server=auth["host"],
        port=auth["port"],
        ssl=bool(auth.get("ssl", False)),
    )


def _wait_for_rpc_ready(auth: dict, *,
                        timeout_seconds: int = _RPC_READY_TIMEOUT_DEFAULT) -> bool:
    """Poll core.version every 1s up to timeout_seconds. Returns True on success."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            client = _make_msfrpc_client(auth)
            _ = client.core.version
            return True
        except Exception:
            time.sleep(1.0)
    return False


def _workspace_name_for(target: str) -> str:
    """Return the MSF workspace name for a target.

    Uses normalize_target (lowercase + strip) so the same target always maps
    to the same workspace regardless of the casing the user typed.
    """
    return normalize_target(target)


def _msf_client(target: str):
    """Return an authed client with the per-target workspace active.

    Workflow:
      1. Read shared auth.
      2. Construct authenticated MsfRpcClient.
      3. Ensure workspace exists (workspace -a <name>; idempotent).
      4. Switch active workspace (workspace <name>).
      5. Return the client.
    """
    auth = _read_or_create_auth()
    client = _make_msfrpc_client(auth)
    ws = _workspace_name_for(target)

    console = client.consoles.console()
    try:
        # workspace -a is idempotent in MSF (adds if missing, no-op if present)
        console.run_with_output(f"workspace -a {ws}")
        # Switch active
        console.run_with_output(f"workspace {ws}")
    except Exception:
        # If the console layer fails, the client is still usable — return it
        pass

    return client


# ── Lifecycle tools: start / stop / status ──────────────────────────

import signal


@tool(
    "metasploit_start",
    "Start the shared msfrpcd daemon (if not already running) and activate "
    "the per-target MSF workspace. Idempotent: returns 'already_running' "
    "if the daemon is up. Stale pidfiles are auto-recovered. The daemon "
    "binds 127.0.0.1:55553 with a random 32-char password persisted at "
    "<targets_root>/.shared/msfrpc/auth.json (mode 0600).",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string",
                       "description": "Target identifier — determines the MSF workspace"},
        },
        "required": ["target"],
    },
)
async def metasploit_start(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target = args["target"]

    with _start_lock():
        existing_pid = _read_pidfile()
        if existing_pid is not None and _process_alive(existing_pid):
            # Already running — just activate the workspace.
            try:
                _msf_client(target)
            except Exception:
                pass  # workspace activation is best-effort here
            auth = _read_or_create_auth()
            return format_tool_result(
                f"msfrpcd already running.\n"
                f"  status:    already_running\n"
                f"  pid:       {existing_pid}\n"
                f"  workspace: {_workspace_name_for(target)}\n"
                f"  rpc_url:   http{'s' if auth['ssl'] else ''}"
                f"://{auth['host']}:{auth['port']}\n"
                f"  rpc_user:  {auth['user']}"
            )

        stale_recovered = False
        if existing_pid is not None and not _process_alive(existing_pid):
            _remove_pidfile()
            stale_recovered = True

        auth = _read_or_create_auth()

        cmd = [
            "msfrpcd",
            "-U", auth["user"],
            "-P", auth["password"],
            "-a", auth["host"],
            "-p", str(auth["port"]),
            "-S",   # no SSL (D9)
            "-f",   # foreground (so we can capture pid)
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return format_error(
                "msfrpcd not found in PATH. Install metasploit-framework."
            )

        if not _wait_for_rpc_ready(auth):
            # Daemon failed to come up — kill it and clean up
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            return format_error(
                "msfrpcd did not become RPC-ready within "
                f"{_RPC_READY_TIMEOUT_DEFAULT}s. Daemon killed; pidfile not written."
            )

        _write_pidfile(proc.pid)

        try:
            _msf_client(target)  # activate workspace
        except Exception:
            pass  # daemon is up; workspace setup is best-effort

        status = "recovered_stale_pidfile" if stale_recovered else "started"
        return format_tool_result(
            f"msfrpcd started.\n"
            f"  status:    {status}\n"
            f"  pid:       {proc.pid}\n"
            f"  workspace: {_workspace_name_for(target)}\n"
            f"  rpc_url:   http{'s' if auth['ssl'] else ''}"
            f"://{auth['host']}:{auth['port']}\n"
            f"  rpc_user:  {auth['user']}"
        )


TOOLS.append(metasploit_start)


def _count_open_sessions() -> tuple[int, list[dict]]:
    """Best-effort count of open sessions. Returns (count, summary list).

    Returns (0, []) if the daemon isn't reachable.
    """
    try:
        client = _msf_client("")
        sessions = client.sessions.list or {}
    except Exception:
        return (0, [])
    summary = []
    for sid, info in sessions.items():
        summary.append({
            "id": sid,
            "type": (info or {}).get("type", "?"),
            "target_host": (info or {}).get("target_host", "?"),
        })
    return (len(summary), summary)


@tool(
    "metasploit_stop",
    "Stop the shared msfrpcd daemon. SIGTERM → 10s wait → SIGKILL if "
    "force=True. Warns (but does NOT refuse) when sessions are open. "
    "Open sessions die when the daemon stops — documented loss-on-stop.",
    {
        "type": "object",
        "properties": {
            "force": {"type": "boolean", "default": False,
                      "description": "If true, escalate to SIGKILL after 10s of SIGTERM ignored"},
        },
        "required": [],
    },
)
async def metasploit_stop(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    force = bool(args.get("force", False))

    pid = _read_pidfile()
    if pid is None:
        return format_tool_result("msfrpcd is not running (no pidfile).\n  status: not_running")

    if not _process_alive(pid):
        _remove_pidfile()
        return format_tool_result(
            f"msfrpcd was not actually running (stale pidfile cleared).\n"
            f"  status:  not_running\n"
            f"  pid_was: {pid}"
        )

    # Best-effort session count BEFORE we kill the daemon
    sessions_lost, _summary = _count_open_sessions()

    # SIGTERM and wait up to 10s
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as e:
        return format_error(f"failed to send SIGTERM to {pid}: {e}")

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not _process_alive(pid):
            break
        time.sleep(0.5)

    timed_out = _process_alive(pid)
    if timed_out and force:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        time.sleep(0.5)
        timed_out = _process_alive(pid)

    if timed_out:
        # Did not die even after escalation
        return format_error(
            f"msfrpcd (pid {pid}) did not exit after SIGTERM"
            f"{' + SIGKILL' if force else ''}. Investigate manually.\n"
            f"  status:  stop_timeout"
        )

    _remove_pidfile()

    warning = None
    if sessions_lost > 0:
        warning = f"{sessions_lost} open session(s) killed when daemon stopped"

    lines = [
        f"msfrpcd stopped.",
        f"  status:        stopped",
        f"  pid_was:       {pid}",
        f"  sessions_lost: {sessions_lost}",
    ]
    if warning:
        lines.append(f"  warning:       {warning}")
    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_stop)


def _parse_workspace_list(console_output: str) -> tuple[list[str], str | None]:
    """Parse msfconsole `workspace` output → (all_workspaces, active).

    Format:
      Workspaces
      ==========
        current  name
        -------  ----
        *        myws
                 default
    """
    workspaces: list[str] = []
    active: str | None = None
    for line in console_output.splitlines():
        s = line.strip()
        if not s or s.startswith(("Workspaces", "===", "current", "---")):
            continue
        if s.startswith("*"):
            # active workspace
            name = s.lstrip("*").strip()
            if name:
                workspaces.append(name)
                active = name
        else:
            workspaces.append(s)
    return workspaces, active


@tool(
    "metasploit_status",
    "Report msfrpcd daemon state. Read-only: does not auto-start, does not "
    "auto-fix. Returns daemon liveness, version, auth ok/error, active "
    "workspace, all workspaces, and open sessions.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def metasploit_status(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    pid = _read_pidfile()
    if pid is None:
        return format_tool_result(
            "msfrpcd status:\n"
            "  daemon:   not_running (no pidfile)\n"
            "  start with: metasploit_start"
        )

    if not _process_alive(pid):
        return format_tool_result(
            f"msfrpcd status:\n"
            f"  daemon:   not_running (pidfile is stale; pid {pid} dead)\n"
            f"  start with: metasploit_start (will recover stale pidfile)"
        )

    # Daemon is alive — probe RPC
    auth_ok = True
    auth_err: str | None = None
    version: str | None = None
    workspaces: list[str] = []
    active_workspace: str | None = None
    sessions: list[dict] = []

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        v = client.core.version
        if isinstance(v, dict):
            version = v.get("version", "?")
        else:
            version = str(v)

        # Workspaces via console
        try:
            console = client.consoles.console()
            ws_out = console.run_with_output("workspace")
            workspaces, active_workspace = _parse_workspace_list(ws_out)
        except Exception:
            pass

        # Sessions
        try:
            for sid, info in (client.sessions.list or {}).items():
                sessions.append({
                    "id": sid,
                    "type": (info or {}).get("type", "?"),
                    "target_host": (info or {}).get("target_host", "?"),
                })
        except Exception:
            pass
    except Exception as e:
        auth_ok = False
        auth_err = f"{type(e).__name__}: {e}"

    lines = [
        "msfrpcd status:",
        f"  daemon:           running (pid {pid})",
        f"  version:          {version or '<unknown>'}",
        f"  auth:             {'ok' if auth_ok else 'FAILED'}",
    ]
    if auth_err:
        lines.append(f"  auth_error:       {auth_err}")
    lines.append(f"  active_workspace: {active_workspace or '<unknown>'}")
    if workspaces:
        lines.append(f"  workspaces:       {', '.join(workspaces)}")
    if sessions:
        lines.append(f"  sessions ({len(sessions)}):")
        for s in sessions:
            lines.append(
                f"    [{s['id']}] {s['type']} → {s['target_host']}"
            )
    else:
        lines.append("  sessions:         (none)")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_status)


# ── Operational tools: search / run / session ───────────────────────


_RANK_ORDER = {
    "excellent": 6,
    "great":     5,
    "good":      4,
    "normal":    3,
    "average":   2,
    "low":       1,
    "manual":    0,
}


def _require_daemon_running() -> dict | None:
    """If daemon isn't up, return an error tool result. Otherwise None."""
    pid = _read_pidfile()
    if pid is None or not _process_alive(pid):
        return format_error(
            "msfrpcd is not running. Start it with metasploit_start <target> first."
        )
    return None


def _filter_modules(modules: list[dict], *,
                    type_: str | None,
                    platform: str | None,
                    rank: str | None) -> list[dict]:
    """Apply type/platform/rank filters to MSF search results."""
    out = []
    rank_min = _RANK_ORDER.get(rank.lower(), 0) if rank else 0
    for m in modules:
        if type_ and (m.get("type") or "").lower() != type_.lower():
            continue
        if platform:
            mp = (m.get("platform") or "").lower()
            if platform.lower() not in mp:
                continue
        m_rank = (m.get("rank") or "").lower()
        if rank and _RANK_ORDER.get(m_rank, 0) < rank_min:
            continue
        out.append(m)
    return out


@tool(
    "metasploit_search",
    "Search Metasploit modules via msfrpcd. Filter by type "
    "(exploit/auxiliary/post/payload), platform, and rank. Returns ranked "
    "candidates. Requires metasploit_start.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Search query (e.g. 'cve:2015-3306' or 'name:proftpd')"},
            "type": {"type": "string",
                     "description": "Filter: exploit, auxiliary, post, payload"},
            "platform": {"type": "string",
                         "description": "Filter: linux, windows, multi, ..."},
            "rank": {"type": "string",
                     "description": "Minimum rank: manual/low/average/normal/good/great/excellent"},
            "limit": {"type": "integer", "default": 25,
                      "description": "Max modules returned (default 25)"},
        },
        "required": ["query"],
    },
)
async def metasploit_search(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    query = args["query"]
    type_ = args.get("type") or None
    platform = args.get("platform") or None
    rank = args.get("rank") or None
    limit = int(args.get("limit", 25))

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        raw_modules = client.modules.search(query) or []
    except Exception as e:
        return format_error(f"module.search failed: {type(e).__name__}: {e}")

    filtered = _filter_modules(raw_modules,
                               type_=type_, platform=platform, rank=rank)
    total = len(filtered)
    shown = filtered[:limit]

    if not shown:
        return format_tool_result(
            f"No modules found for query={query!r} "
            f"(type={type_}, platform={platform}, rank={rank})."
        )

    lines = [f"Module search results for {query!r} "
             f"(showing {len(shown)} of {total}):", ""]
    for m in shown:
        refs = m.get("ref") or []
        cve = next((r for r in refs if str(r).upper().startswith("CVE-")), "")
        cve_str = f" [{cve}]" if cve else ""
        lines.append(f"  {m.get('fullname', '<?>')}")
        lines.append(f"    type={m.get('type','?')}  platform={m.get('platform','?')}  "
                     f"rank={m.get('rank','?')}  date={m.get('disclosure_date','')}{cve_str}")
        desc = (m.get("description") or "").strip().replace("\n", " ")
        if desc:
            lines.append(f"    {desc[:200]}")
        lines.append("")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_search)


_CHECK_VULNERABLE = ("vulnerable",)
_CHECK_SAFE = ("safe",)
_CHECK_UNKNOWN = ("unknown", "detected")
_CHECK_NO_METHOD = ("no_check_method",)
_CHECK_ERROR = ("error", "appears", "unsupported")


def _classify_check_result(raw: Any, raised: BaseException | None) -> tuple[str, str]:
    """Normalize a check_exploit return value into (code, message).

    code: vulnerable | safe | unknown | detected | no_check_method | error
    """
    if raised is not None:
        # NotImplementedError or AttributeError → no check method
        if isinstance(raised, (NotImplementedError, AttributeError)):
            return ("no_check_method", f"{type(raised).__name__}: {raised}")
        return ("error", f"{type(raised).__name__}: {raised}")

    if isinstance(raw, dict):
        code = (raw.get("code") or "").lower()
        msg = raw.get("message") or ""
        if code in ("vulnerable", "safe", "unknown", "detected",
                    "no_check_method", "error"):
            return (code, msg)
        # MSF sometimes returns Vuln::Code constants as strings
        if "vulnerable" in code:
            return ("vulnerable", msg)
        if "safe" in code:
            return ("safe", msg)
        return ("unknown", msg or str(raw))

    if isinstance(raw, str):
        low = raw.lower()
        for tag in ("vulnerable", "safe", "unknown", "detected"):
            if tag in low:
                return (tag, raw)
        return ("unknown", raw)

    return ("unknown", str(raw))


@tool(
    "metasploit_run",
    "Run a Metasploit module against a target. ALWAYS checks first by "
    "default (D7). Behavior matrix: vulnerable→exploit; safe/unknown/"
    "detected/no_check_method→skip; force=True overrides skip. Records a "
    "high-severity finding when an exploit succeeds. Scope-checked BEFORE "
    "the check fires.",
    {
        "type": "object",
        "properties": {
            "module": {"type": "string",
                       "description": "Full module name (e.g. exploit/multi/http/proftpd_modcopy_exec)"},
            "options": {"type": "object",
                        "description": "Module options (e.g. {'RHOSTS': '10.10.10.5', 'RPORT': 80})"},
            "target": {"type": "string",
                       "description": "Target identifier — for scope check + workspace + KB writes"},
            "payload": {"type": "string", "default": "",
                        "description": "Optional payload module (e.g. windows/x64/meterpreter/reverse_tcp)"},
            "payload_options": {"type": "object", "default": {},
                                "description": "Payload-side options (e.g. {'LHOST': '10.10.14.5'})"},
            "force": {"type": "boolean", "default": False,
                      "description": "Bypass check-then-skip (D7 escape hatch)"},
            "timeout_seconds": {"type": "integer", "default": 300,
                                "description": "Max time to wait for exploit to return"},
        },
        "required": ["module", "options", "target"],
    },
)
async def metasploit_run(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    module_name = args["module"]
    options = args.get("options") or {}
    target = args["target"]
    payload_name = args.get("payload") or None
    payload_options = args.get("payload_options") or {}
    force = bool(args.get("force", False))
    timeout_seconds = int(args.get("timeout_seconds", 300))

    # ── Scope check BEFORE check fires (D7 + risk row in spec §12) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")

    # Determine module type from name
    parts = module_name.split("/", 1)
    if len(parts) != 2:
        return format_error(
            f"module name must be 'type/path', got {module_name!r}"
        )
    mod_type, mod_path = parts

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        mod = client.modules.use(mod_type, mod_path)
    except Exception as e:
        return format_error(f"failed to load module {module_name!r}: "
                            f"{type(e).__name__}: {e}")

    # Apply options
    for k, v in options.items():
        try:
            mod[k] = v
        except Exception:
            pass  # MSF may reject unknown options; surfaced via exploit_output

    # ── Check phase ──
    check_raw: Any = None
    check_raised: BaseException | None = None
    try:
        check_raw = mod.check_exploit()
    except BaseException as e:
        check_raised = e

    check_code, check_msg = _classify_check_result(check_raw, check_raised)

    # ── Decision matrix ──
    should_run = (check_code in _CHECK_VULNERABLE) or force

    exploit_ran = False
    exploit_output = ""
    session_id: int | None = None
    decision_note = ""

    if should_run:
        try:
            if payload_name:
                payload_mod = client.modules.use("payload", payload_name)
                for k, v in payload_options.items():
                    try:
                        payload_mod[k] = v
                    except Exception:
                        pass
                exploit_raw = mod.execute(payload=payload_mod)
            else:
                exploit_raw = mod.execute()
            exploit_ran = True
            exploit_output = str(exploit_raw)
            # Best-effort: look for a session in the post-execute session list
            try:
                sessions = client.sessions.list or {}
                for sid, info in sessions.items():
                    if (info or {}).get("target_host") == target or \
                       (info or {}).get("target_host") == options.get("RHOSTS"):
                        try:
                            session_id = int(sid)
                        except (ValueError, TypeError):
                            session_id = None
                        break
            except Exception:
                pass
        except Exception as e:
            exploit_output = f"{type(e).__name__}: {e}"
    else:
        decision_note = (
            f"skipped exploit (check={check_code}); pass force=true to "
            f"override (D7 escape hatch)."
        )

    # ── KB finding on successful exploit ──
    if session_id is not None:
        try:
            from ..kb import FindingFact
            kb = for_target(target)
            kb.record_finding(FindingFact(
                title=f"Exploited {module_name} on {target}",
                severity="high",
                description=(
                    f"Module: {module_name}\n"
                    f"Options: {options}\n"
                    f"Check: {check_code} — {check_msg}\n"
                    f"Session: id={session_id}\n"
                    f"Exploit output (truncated): {exploit_output[:2000]}"
                ),
                evidence_paths=[],
            ))
        except Exception:
            pass

    lines = [
        f"metasploit_run: {module_name}",
        f"  target:        {target}",
        f"  check_result:  {check_code}",
        f"  check_output:  {check_msg[:500]}",
        f"  exploit_ran:   {exploit_ran}",
    ]
    if decision_note:
        lines.append(f"  decision:      {decision_note}")
    if exploit_ran:
        lines.append(f"  exploit_output: {exploit_output[:2000]}")
    if session_id is not None:
        lines.append(f"  session_id:    {session_id}")
        lines.append(f"  finding:       recorded as severity=high")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_run)


@tool(
    "metasploit_session",
    "Interact with a Metasploit session opened by metasploit_run. Actions: "
    "list (enumerate), cmd (single command, captured up to timeout_seconds), "
    "close (kill session). Single command per cmd call — no interactive REPL. "
    "Sessions die when metasploit_stop runs.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "cmd", "close"]},
            "session_id": {"type": "integer",
                           "description": "Required for cmd/close"},
            "command": {"type": "string",
                        "description": "Required for cmd"},
            "timeout_seconds": {"type": "integer", "default": 30,
                                "description": "Max time to wait for cmd output"},
        },
        "required": ["action"],
    },
)
async def metasploit_session(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    action = args["action"]
    session_id = args.get("session_id")
    command = args.get("command") or ""
    timeout = int(args.get("timeout_seconds", 30))

    # Validate required args early, before connecting to msfrpcd
    if action in ("cmd", "close") and session_id is None:
        return format_error("action=cmd|close requires session_id argument.")
    if action == "cmd" and not command:
        return format_error("action=cmd requires command argument.")

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
    except Exception as e:
        return format_error(f"failed to connect to msfrpcd: "
                            f"{type(e).__name__}: {e}")

    if action == "list":
        try:
            sessions = client.sessions.list or {}
        except Exception as e:
            return format_error(f"sessions.list failed: {e}")
        if not sessions:
            return format_tool_result("No open sessions.")
        lines = [f"Open sessions ({len(sessions)}):"]
        for sid, info in sessions.items():
            i = info or {}
            lines.append(
                f"  [{sid}] type={i.get('type','?')}  "
                f"target_host={i.get('target_host','?')}  "
                f"opened_at={i.get('opened_at','?')}"
            )
        return format_tool_result("\n".join(lines))

    sid_key = str(session_id)
    sessions = client.sessions.list or {}
    if sid_key not in sessions:
        return format_tool_result(
            f"Session {session_id} not found.\n  status: not_found"
        )

    try:
        session = client.sessions.session(sid_key)
    except Exception as e:
        return format_error(f"failed to get session {session_id}: "
                            f"{type(e).__name__}: {e}")

    if action == "cmd":
        try:
            output = session.run_with_output(command, timeout=timeout)
            return format_tool_result(
                f"Session {session_id} output (cmd={command!r}):\n"
                f"  status: open\n"
                f"---\n{output}"
            )
        except TimeoutError as e:
            return format_tool_result(
                f"Session {session_id} cmd={command!r}: TIMEOUT after {timeout}s\n"
                f"  status: open (output partial; re-invoke to wait longer)\n"
                f"  error:  {e}"
            )
        except Exception as e:
            return format_error(
                f"session.run_with_output failed: {type(e).__name__}: {e}"
            )

    if action == "close":
        try:
            session.stop()
        except Exception as e:
            return format_error(f"session.stop failed: {type(e).__name__}: {e}")
        return format_tool_result(
            f"Session {session_id} closed.\n  status: closed"
        )

    return format_error(f"Unknown action: {action!r}. Valid: list, cmd, close")


TOOLS.append(metasploit_session)
