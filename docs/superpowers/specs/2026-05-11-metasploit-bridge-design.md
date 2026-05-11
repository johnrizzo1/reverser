# Metasploit bridge — design

**Date:** 2026-05-11
**Status:** Design approved; ready for implementation plan
**Roadmap entry:** Top 5 #1 (was #2): "Add searchsploit + msfvenom + Metasploit RPC bridge"
**Predecessor specs:** AD capability pack (2026-05-03), manager profile (2026-05-09), stop & resume (2026-05-09)

---

## 1. Goal

Close the "find a known public exploit, generate a payload, try it" loop that is
currently entirely manual. Three external tools wrap into the harness:

- **searchsploit** — local exploit-db CVE/keyword search
- **msfvenom** — Metasploit payload generator (CLI)
- **Metasploit RPC** (`msfrpcd` daemon) — module search / configure / check /
  exploit / session interaction via JSON-RPC

A new `exploit` profile joins the dispatchable specialty pool so the manager
profile can dispatch `exploit` specialists with a hypothesis like "CVE-X is
exploitable on host Y" and get back a structured outcome.

## 2. Non-goals (v1)

- Multi-stage exploitation (session migration, EoP chaining) — that's the
  lead's job after a foothold opens; specialist returns control
- Custom MSF module loading — upstream modules only
- Encoder selection beyond the user-supplied `encoder=` arg (no auto-evasion)
- Meterpreter scripting beyond single-command session interaction
- Remote msfrpcd — localhost only
- TLS for the RPC connection — localhost-only obviates it
- Session persistence across `metasploit_stop` — sessions die; documented
- Per-target msfrpcd processes — shared daemon + per-target workspace instead

## 3. Architectural decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Shared `msfrpcd` daemon + per-target MSF workspace | MSF's intended multi-target model. Heavy daemon (~500MB RAM, ~30s startup) runs once, workspaces partition `hosts`/`services`/`creds`/`loot` per target inside it. Lifecycle survives across reverser sessions. |
| D2 | New module `src/reverser/tools/metasploit.py` holds all 8 tools | Existing `exploit.py` is taken (ROPgadget binary RE). All three external tools (searchsploit, msfvenom, msfrpc) are one conceptual capability cluster; one file. |
| D3 | KB integration = reuse `findings` + `artifacts` + `hypotheses` + `notes` | No new tables. Confirmed exploits → `FindingFact`; generated payloads → `ArtifactFact`; exploit candidates → `HypothesisFact`; search summaries → notes. |
| D4 | New profile `profiles/exploit.py` joins the dispatch pool | Manager profile dispatches `exploit` specialists for the search→pick→run loop. `_DISPATCHABLE_SPECIALTIES` grows 5 → 6. |
| D5 | Manager allowlist unchanged (no msf tools) | Same posture as `netexec_*` / `bloodhound_*`: heavy offensive tools go through dispatch. Manager doesn't run msf directly. |
| D6 | 8 tools total, AD-pack granularity | Matches `netexec_*` (6 tools) + `bloodhound_*` (6 tools) cadence. Each tool maps to a meaningful agent decision point. |
| D7 | `metasploit_run` always-check-first by default; `force=True` to override | Most modules implement `check`; auto-running it prevents wasted budget on doomed attempts. `force=True` covers no-check-method modules. |
| D8 | Daemon auth = random 32-char password generated on first start, stored at `<targets_root>/.shared/msfrpc/auth.json` mode 0600 | Persistent across reverser processes; no version-control leak; not hardcoded. |
| D9 | RPC bound to `127.0.0.1`, no TLS | Localhost-only daemon; TLS adds complexity for zero benefit. Remote daemon is v2. |
| D10 | `metasploit_stop` warns when sessions are open; does not refuse | Documented loss-on-stop behavior; user makes the call. |
| D11 | Concurrent `metasploit_start` serialized via `fcntl.flock` | Two reverser processes starting against different targets won't race the daemon spawn. Linux + macOS supported. |
| D12 | Payloads written to `targets/<target>/loot/payloads/<name>-<sha8>.<ext>` | Same `loot/` dir AD pack uses for NTDS dumps and BloodHound zips. `ArtifactFact` indexes them with sha256. |

## 4. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ reverser i -p exploit 10.10.10.5  (or manager dispatches)          │
│                                                                    │
│   Exploit specialist (sub-agent or direct user)                    │
│     │                                                              │
│     ├── searchsploit_search ── CLI (exploitdb package)             │
│     ├── msfvenom_generate ──── CLI ─→ targets/<t>/loot/payloads/   │
│     │                                  + KB ArtifactFact           │
│     ├── metasploit_start ─────┐                                    │
│     │                         │                                    │
│     ├── metasploit_stop ──────┤                                    │
│     │                         │                                    │
│     ├── metasploit_status ────┤                                    │
│     │                         │                                    │
│     ├── metasploit_search ────┼─→ pymetasploit3 ─→ msfrpcd (RPC)   │
│     │                         │                  127.0.0.1:55553   │
│     ├── metasploit_run ───────┤                                    │
│     │                         │       │                            │
│     └── metasploit_session ───┘       │                            │
│                                       │                            │
│   On confirmed exploit:               │                            │
│     - kb_add_finding(severity=high)   │                            │
│     - kb_add_artifact for payload     │                            │
│     - kb_update_hypothesis(confirmed) │                            │
│                                       ▼                            │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │ msfrpcd (shared daemon)                                   │     │
│  │   workspace=10.10.10.5 (per-target partition)             │     │
│  │   hosts/services/creds/loot scoped to workspace           │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │ targets/.shared/msfrpc/                                  │     │
│  │   auth.json   (0600: user/password/host/port/ssl)        │     │
│  │   pidfile     (cleared on clean stop)                    │     │
│  │   auth.json.lock  (fcntl serialization)                  │     │
│  └──────────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────────┘
```

## 5. Tool surface (8 tools)

### 5.1 searchsploit_search

```
searchsploit_search(
    query: str,                       # "CVE-2022-12345" or "ProFTPD"
    *,
    cve_only: bool = False,           # --cve
    title_only: bool = True,          # --title (saner default)
    target: str | None = None,        # optional; KB-record results
    limit: int = 30,
) -> {summary: str, candidates: list[{exploit_id, title, type, platform, date, path, url}]}
```

Shells out to `searchsploit -j` (JSON output) for parseability. If `target` is
given, writes a `note` capturing the candidate list. No new KB schema.

### 5.2 msfvenom_generate

```
msfvenom_generate(
    payload: str,                     # "windows/x64/meterpreter/reverse_tcp"
    *,
    lhost: str,
    lport: int = 4444,
    format: str = "exe",
    target: str,                      # required for loot dir + KB
    encoder: str | None = None,
    iterations: int = 1,
    bad_chars: str | None = None,
    options: dict[str, str] | None = None,
) -> {path: str, sha256: str, summary: str}
```

Writes to `targets/<target>/loot/payloads/<payload-mangled>-<sha8>.<ext>`.
Records `ArtifactFact(kind="payload", path=..., sha256=...,
source_tool="msfvenom")`.

### 5.3 metasploit_start

```
metasploit_start(target: str) -> {
    status: "started" | "already_running" | "recovered_stale_pidfile",
    pid: int,
    workspace: str,
    rpc_url: str,
    rpc_user: str,
}
```

Acquires `fcntl.flock(auth.json.lock)`. Reads or creates `auth.json` (32-char
url-safe password). Reads pidfile; if alive returns `already_running`. If stale
(pidfile but dead process), removes pidfile and proceeds, returns
`recovered_stale_pidfile`. Spawns:

```
msfrpcd -U <user> -P <password> -a 127.0.0.1 -p 55553 -S -f
```

with `start_new_session=True` (survives parent exit). Polls `core.version`
every 1s up to 60s for RPC readiness. Authenticates via `pymetasploit3.MsfRpcClient`,
runs `workspace -a <target>` (idempotent), switches active workspace, returns
the structured payload.

### 5.4 metasploit_stop

```
metasploit_stop(force: bool = False) -> {
    status: "stopped" | "not_running" | "stop_timeout",
    pid_was: int | None,
    sessions_lost: int,                # warning surfaced when > 0
    warning: str | None,
}
```

Reads pidfile. SIGTERM → 10s wait → SIGKILL if `force=True`. If sessions are
open at stop time: warning surfaced in the response (e.g. `"sessions_lost": 3,
"warning": "3 open sessions will be killed"`), but `metasploit_stop` does not
refuse to stop without `force=True`. Removes pidfile on success.

### 5.5 metasploit_status

```
metasploit_status() -> {
    daemon: {running: bool, pid: int | None, uptime_seconds: int | None},
    auth: {ok: bool, user: str | None, error: str | None},
    active_workspace: str | None,
    workspaces: list[str],
    sessions: list[{id, type, target_host, opened_at}],
}
```

Read-only probe. Does NOT auto-start. Does NOT auto-fix. Returns whatever state
the daemon is in.

### 5.6 metasploit_search

```
metasploit_search(
    query: str,                       # "cve:2022-12345" or "name:proftpd"
    *,
    type: str | None = None,          # exploit/auxiliary/post/payload
    platform: str | None = None,
    rank: str | None = None,          # excellent/great/good/normal/average/low
    limit: int = 25,
) -> {modules: list[{fullname, type, platform, rank, disclosure_date, cve, description}]}
```

Calls MSF `module.search`. Returns ranked candidates. No KB writes (agent
inspects results).

### 5.7 metasploit_run

```
metasploit_run(
    module: str,                      # "exploit/multi/http/proftpd_modcopy_exec"
    options: dict[str, Any],          # {"RHOSTS": ..., "RPORT": ...}
    *,
    target: str,
    payload: str | None = None,
    payload_options: dict | None = None,
    force: bool = False,
    timeout_seconds: int = 300,
) -> {
    check_result: "vulnerable" | "safe" | "unknown" | "detected" |
                  "no_check_method" | "error",
    check_output: str,
    exploit_ran: bool,
    exploit_output: str,
    session_id: int | None,
    error: str | None,
}
```

**Behavior matrix (always-check-first default):**

| Check result          | Default behavior                | With `force=True`   |
|-----------------------|---------------------------------|---------------------|
| `vulnerable`          | Run exploit                     | Run exploit         |
| `safe`                | Skip exploit, return check      | Run exploit         |
| `unknown` / `detected`| Skip exploit, return check      | Run exploit         |
| `no_check_method`     | Skip exploit (no auto-fire)     | Run exploit         |

Scope-checked via `load_scope(target).assert_in_scope(target)` **before** check
fires (not just before exploit). On successful exploit (`session_id` returned):

```
kb.record_finding(FindingFact(
    severity="high",
    title=f"Exploited {module} on {target}",
    description=<truncated exploit output>,
    evidence_paths=[],
))
```

### 5.8 metasploit_session

```
metasploit_session(
    action: "list" | "cmd" | "close",
    *,
    session_id: int | None = None,    # required for cmd/close
    command: str | None = None,       # required for cmd
    timeout_seconds: int = 30,
) -> {
    sessions: list[...] | None,       # for action=list
    output: str | None,               # for action=cmd
    status: "open" | "closed" | "not_found",
}
```

Single command per `cmd` call (no interactive REPL). Output captured for up to
`timeout_seconds`, then returned (marked partial if timeout). Sessions die when
`metasploit_stop` runs.

## 6. Daemon lifecycle (helper functions)

```python
# All in src/reverser/tools/metasploit.py

def _msf_state_dir() -> Path:
    """Returns _targets_root() / '.shared' / 'msfrpc'.
    Created on first access with mode 0700."""

def _read_or_create_auth() -> dict:
    """Read auth.json, or generate 32-char password + write 0600 if missing."""

def _read_pidfile() -> int | None: ...
def _write_pidfile(pid: int) -> None: ...
def _remove_pidfile() -> None: ...
def _process_alive(pid: int) -> bool:
    """os.kill(pid, 0); catches OSError/ProcessLookupError → False."""

def _wait_for_rpc_ready(auth: dict, timeout_seconds: int = 60) -> bool:
    """Poll core.version every 1s; return True when reachable."""

def _msf_client(target: str) -> MsfRpcClient:
    """Auth + workspace activation. Called by every operational tool."""

@contextmanager
def _start_lock():
    """fcntl.flock on auth.json.lock for the duration of start. Linux/macOS."""
```

## 7. Exploit profile

`src/reverser/profiles/exploit.py`. New dispatchable specialty.

```python
PROFILE_EXPLOIT = _register(Profile(
    name="Exploit",
    key="exploit",
    description="Public-exploit hunter: searchsploit + msfvenom + Metasploit RPC bridge",
    system_addendum=SYSTEM_ADDENDUM,
    skills=[SKILL_HUNT, SKILL_GENERATE_PAYLOAD, SKILL_TRY_EXPLOIT,
            SKILL_HANDLE_SESSION, SKILL_REPORT, SKILL_WRAP_UP],
    tools_allowlist=None,
))
```

### Skills

| Key | Name              | Prompt focus |
|-----|-------------------|--------------|
| `h` | Hunt              | searchsploit_search + metasploit_search; cross-reference; pick top 3 by rank+platform; kb_add_hypothesis for each |
| `g` | Generate payload  | msfvenom_generate; default LHOST = outbound iface; LPORT = 4444 |
| `t` | Try exploit       | metasploit_run on highest-confidence unconfirmed hypothesis; honor always-check-first; ask before force= |
| `i` | Handle session    | metasploit_session list/cmd; characterize foothold (whoami, id, hostname, ipconfig); evidence_refs on hypothesis |
| `r` | Report            | kb_export_report including confirmed exploits + payload artifacts |
| `w` | Wrap up           | mark unresolved hypotheses 'abandoned'; final report; tell user /done |

### System addendum (abridged)

```
## Profile: Exploit (public-exploit specialist)

You are an exploit-hunting specialist. Find public exploits for software/CVEs
the target is running, generate payloads as needed, attempt exploitation
through Metasploit. Report back to the engagement lead.

### Workflow

1. Read the per-target KB (kb_show) for hosts/services/findings/notes.
2. Gather CVE/software hints from KB or dispatch context.
3. searchsploit_search for each hint. Reject candidates older than 5 years
   unless target software is also that old.
4. metasploit_search for matching modules. Filter by platform + rank
   (excellent > great > good > normal > average).
5. For each candidate worth trying, kb_add_hypothesis with the CVE/module.
6. Pick highest-confidence hypothesis. metasploit_run with default
   check-then-exploit. Update the hypothesis with outcome.
7. On session opened: characterize foothold via metasploit_session
   action=cmd. Record as evidence_refs.
8. On session NOT opened: mark hypothesis refuted, move to next candidate.
9. After 3 failed attempts: stop, summarize, report back.

### Hard rules

- Always check first (metasploit_run default). Override only when
  check_method is missing AND you have high confidence.
- Honor scope.toml. metasploit_run enforces it at the tool layer too.
- Don't run modules with rank=manual or rank=low without explicit
  user approval.
- Generated payloads land in targets/<target>/loot/payloads/. Never
  write payloads anywhere else.
- Don't metasploit_start unless searchsploit alone is insufficient
  (msfrpcd is heavy; only spin it up when you need RPC).
```

## 8. Manager profile changes

### `src/reverser/tools/dispatch.py`

```python
_DISPATCHABLE_SPECIALTIES = (
    "pentest", "ad", "webpentest", "webapi", "webrecon",
    "exploit",  # NEW
)
```

### `src/reverser/profiles/manager.py`

Specialist menu paragraph added to `SYSTEM_ADDENDUM`:

```
- **`exploit`** — public-exploit hunter: searchsploit + msfvenom +
  Metasploit RPC. Dispatch when you have a CVE-or-software-version
  hypothesis to test (e.g. "CVE-2022-XXXX is exploitable on this host").
  The specialist runs the search → pick → check-then-exploit → session
  loop and reports back with confirmed/refuted outcome.
```

Manager allowlist (17 tools) unchanged. No msf tools added.

## 9. File change set

### Add

| Path | Purpose |
|---|---|
| `src/reverser/tools/metasploit.py` | 8 MCP tools + lifecycle helpers |
| `src/reverser/profiles/exploit.py` | New dispatchable specialty |
| `tests/test_metasploit_helpers.py` | Pure helpers |
| `tests/test_metasploit_lifecycle.py` | Start/stop/status (mocked subprocess) |
| `tests/test_metasploit_operations.py` | Search/run/session (mocked pymetasploit3) |
| `tests/test_searchsploit.py` | searchsploit CLI wrapper |
| `tests/test_msfvenom.py` | Payload generation + KB artifact write |
| `tests/test_profiles_exploit.py` | Profile registration assertions |
| `tests/manual/exploit_smoke.md` | 30-min walkthrough vs HTB box |

### Modify

| Path | Change |
|---|---|
| `src/reverser/tools/__init__.py` | Register 8 new tools (ALL_TOOLS 69 → 77) |
| `src/reverser/tools/dispatch.py` | `_DISPATCHABLE_SPECIALTIES` += "exploit" |
| `src/reverser/profiles/__init__.py` | Import exploit module |
| `src/reverser/profiles/manager.py` | Specialist menu paragraph |
| `devenv.nix` | `+ metasploit-framework`, `+ exploitdb`, venv `+ pymetasploit3` |
| `CAPABILITY_ROADMAP.md` | Mark Top 5 #1 ✅ shipped with status note |
| `README.md` | exploit profile row in profiles table; usage section |
| `tests/test_tool_registry.py` | `ALL_TOOLS == 77` assertion |

### Does not change

- KB schema (`hypotheses`, `findings`, `artifacts`, `notes` already cover this)
- Backends (`ClaudeBackend`, `OpenAICompatBackend`)
- TUI app structure
- `kb/scope.py` (existing `assert_in_scope` covers metasploit_run)

## 10. Testing strategy

### Unit (no daemon, no SDK)

- `test_metasploit_helpers.py` — auth.json roundtrip with 0600 perms; pidfile
  read/write/remove; stale-pidfile detection; payload path mangling
  (sha8 prefix + format extension); workspace name normalization.
- `test_searchsploit.py` — mock `subprocess.run` returning `searchsploit -j`
  JSON; verify candidates parsed; KB note write when `target=` given.
- `test_msfvenom.py` — mock `subprocess.run`; verify file written to
  `targets/<target>/loot/payloads/`; verify `ArtifactFact` recorded with
  correct sha256.
- `test_profiles_exploit.py` — exploit profile registered; key="exploit";
  6 skills with keys h/g/t/i/r/w; system_addendum mentions
  searchsploit/msfvenom/metasploit/CVE.

### Integration (mocked daemon + pymetasploit3)

- `test_metasploit_lifecycle.py` — `metasploit_start` flow with mocked
  `subprocess.Popen` + mocked `_wait_for_rpc_ready`; verify auth file
  generation, pidfile creation, workspace activation; `metasploit_stop`
  with sessions-open warning behavior; `metasploit_status` reports
  daemon-dead correctly when pidfile is stale; `fcntl.flock` serializes
  concurrent starts.
- `test_metasploit_operations.py` — mock `pymetasploit3.MsfRpcClient`;
  verify `metasploit_search` parses module list; verify `metasploit_run`
  check-then-exploit decision matrix (each row of Section 5.7 table);
  verify auto-finding write on successful exploit; verify scope.toml
  enforcement BEFORE check (not just before exploit); verify
  `metasploit_session` cmd timeout marks partial.

### Manual smoke (out-of-suite)

`tests/manual/exploit_smoke.md` — 30-min walkthrough against an HTB box
known to be exploitable via a public CVE:

1. `metasploit_start` — daemon up, workspace created
2. `searchsploit_search <CVE>` — hits returned
3. `metasploit_search "cve:..."` — modules returned
4. `metasploit_run` — check → exploit → session opens
5. `metasploit_session action=cmd command=whoami` — output captured
6. `kb_show` — finding + artifact recorded
7. `metasploit_session action=close` — clean close
8. `metasploit_stop` — warning re open sessions, then proceed

## 11. Devenv changes

```nix
# in packages list (cross-platform)
metasploit-framework   # msfconsole, msfrpcd, msfvenom
exploitdb              # provides searchsploit CLI + the exploit database
```

```nix
# in languages.python.venv.requirements
pymetasploit3          # RPC client; pure Python
```

Verified 2026-05-11: both nixpkgs attributes exist, `platforms = lib.platforms.unix`,
neither marked broken. Same `gh api` verification approach used before
ffuf/nuclei/sqlmap to avoid the enum4linux-ng-style surprise.

If `metasploit-framework` fails to build on Darwin (Ruby+openssl is occasionally
fragile), fallback is `brew install metasploit` + doc-comment, same pattern as
the enum4linux-ng workaround.

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| msfrpcd takes >60s to start on slow boxes | `_wait_for_rpc_ready` timeout configurable; clear error on timeout |
| pymetasploit3 ABI breakage on new MSF version | Pin version in venv requirements; gracefully surface auth/RPC errors |
| Exploit fires before scope check | `metasploit_run` calls `scope.assert_in_scope` BEFORE check, not just before exploit |
| Payload committed to git accidentally | `targets/<target>/loot/` already in `.gitignore`; verify before plan commit |
| Daemon orphaned after parent process death | `start_new_session=True` is deliberate; operator must `metasploit_stop` |
| Concurrent start race | `fcntl.flock` on auth.json.lock serializes; Linux + macOS only |
| Open sessions lost on stop | Documented; warning surfaced in `metasploit_stop` response |
| Module rank=manual or =low silently exploited | System addendum hard rule: don't run without explicit user approval |

## 13. Future work (v2+)

- Remote `msfrpcd` (TLS + cert pinning required)
- Auto-encoder selection for AV evasion
- Session migration / privilege-escalation chaining as a specialist skill
- Multi-stage exploitation orchestration (currently the lead's job)
- Per-target msfrpcd if shared workspace contention ever shows up in practice
- Custom MSF module loading
- `metasploit_run` resume after timeout (current behavior: timeout returns
  partial; agent re-invokes)
- An `exploit_candidates` KB table if hypothesis-noise from search results
  becomes problematic
