# reverser

An AI-powered agent for reverse engineering and penetration testing. Give it a binary or a network target and it works autonomously — or sit at the interactive TUI and direct it through a real engagement.

**91 MCP tools** wired across binary RE, network pentest, Active Directory, web pentest, exploit hunting, and browser automation. **15 profiles** that specialize the system prompt and tool surface for different target types. Local model support (LM Studio, Ollama, any OpenAI-compatible endpoint) alongside Claude.

---

## What it can do

### Binary reverse engineering
- Static and dynamic analysis with **radare2** (r2pipe), **Ghidra**, **GDB**, **strace**, **objdump**, **nm**, **readelf**, **binwalk**, **strings**, **checksec**, **pe_info**
- Symbolic execution with **angr**, gadget hunting with **ROPgadget**, raw bytes disassembly with **capstone**
- Decompilation: **r2 ghidra plugin**, **procyon** (Java), **jadx** (Android)
- Sandboxed binary execution: `run_binary`, **wine** for Windows PE binaries on Linux/macOS

### Network penetration testing
- **nmap** with version detection + script support, **dns_recon**, **nbtscan**, **smb_enum**, **kerberos_enum** (AS-REP/Kerberoast), **ldap_search**, **enum4linux-ng**
- Full **NetExec** wrapper: SMB / WinRM / LDAP / MSSQL / SSH / FTP+WMI with check_auth / spray / exec / SAM/LSA/NTDS dump
- **BloodHound** stack: per-target Neo4j, bloodhound-python collector, 15 canned cypher queries, free-form cypher
- **Metasploit RPC bridge**: searchsploit_search + msfvenom_generate + metasploit_{start,stop,status,search,run,session}. Always-check-first behavior; auto-finding write on successful exploit; per-target MSF workspaces

### Web application pentesting
- Scanners: **nuclei**, **nikto**, **ffuf**, **gobuster**, **subfinder**, **wafw00f**, **testssl**, **sqlmap**, **whatweb**, **ssl_scan**
- **Playwright browser automation** (14 tools): launch headless Chromium with `web_browser_start`, navigate / click / fill_form / evaluate / wait_for, JS-aware SPA crawling with `web_browser_crawl`, XSS confirmation with dialog/sentinel/console heuristic via `web_browser_confirm_xss`, automatic screenshot evidence into `targets/<target>/findings/<id>/screenshot-<n>.png`

### Per-target persistent knowledge base
- SQLite KB at `targets/<target>/state.db` tracking hosts, services, credentials, findings, hypotheses, artifacts, notes
- **Credential lifecycle**: discovered creds are validated across SMB/WinRM/LDAP/MSSQL/SSH automatically; status transitions tracked
- **Hypothesis tree**: every engagement is structured as falsifiable hypotheses with status (proposed / testing / confirmed / refuted / abandoned), dispatch counts, evidence refs
- **Scope envelope**: optional `targets/<target>/scope.toml` with in_scope_cidrs / no_dos / no_account_lockout / allowed_hours — enforced by every offensive tool

### Engagement orchestration
- **Manager profile** dispatches specialist sub-agents (`ad`, `pentest`, `webpentest`, `webapi`, `webrecon`, `exploit`) via the SDK Task primitive. Maintains the hypothesis tree as the engagement plan.
- **K-failure pivot discipline** baked into prompts: manager K=2, pentest K=3, webpentest K=5. After K failed attempts against a hypothesis, the agent is required to mark it refuted and propose three orthogonal alternatives.
- **Connection-failure circuit breaker**: after 3 conn errors against the same target across any tool family, the next call returns an error and the prompt directs the agent to yield to the user. Resets only on user input.
- **Allowlist enforcement**: manager profile's tool surface is enforced at the `execute_tool` boundary, preventing the model from inventing tool names outside the profile.
- **Stop & resume sessions**: multi-day engagements survive process exit / machine restart. Per-turn autosave + atexit/SIGTERM emergency snapshot + conversation replay on resume.
- **Multi-backend**: Claude (default), Ollama, LM Studio, or any OpenAI-compatible endpoint.

---

## Prerequisites

- [devenv](https://devenv.sh/getting-started/#installation)
- One of:
  - `ANTHROPIC_API_KEY` environment variable (for Claude backend), OR
  - [Ollama](https://ollama.com/) running locally, OR
  - [LM Studio](https://lmstudio.ai/) running locally (default port 1234), OR
  - any other OpenAI-compatible endpoint (vLLM, llama.cpp, etc.)
- Wine (optional, for analyzing Windows PE binaries on Linux/macOS)
- First-time devenv shell entry will install Chromium for Playwright (~150MB, one-time) to `~/.cache/ms-playwright/`

## Setup

```sh
devenv shell
```

This drops you into a shell with 60+ tools (radare2, rizin, ghidra, gdb, strace, binwalk, yara, angr, nmap, ffuf, nuclei, sqlmap, metasploit, exploitdb, neo4j, etc.) and installs the `reverser` CLI in editable mode.

For penetration testing tools (anything that touches the network), set:

```sh
export REVERSER_PENTEST_AUTHORIZED=1
# or create .reverser-authorized in the project root
```

This is the explicit acknowledgement that you have written authorization to test the target.

---

## Usage

### Autonomous binary analysis

```sh
# Quick triage — file type, security, strings, headers
reverser triage <binary>

# Full analysis — triage + static + dynamic + report
reverser analyze <binary>

# Solve a crackme / CTF challenge
reverser solve <binary>
```

### Interactive TUI

Launch a conversational interface where you guide the agent, use skills, and switch profiles:

```sh
# Default (general) profile
reverser interactive <binary>
reverser i <binary>              # short alias

# Pick a profile for your target type
reverser i <binary> --profile ctf
reverser i <binary> -p android
reverser i https://example.com -p webpentest
REVERSER_PENTEST_AUTHORIZED=1 reverser i 10.10.10.5 -p manager
```

Inside the TUI:
- Type messages to chat with the agent
- Press **F1** to run a skill (Triage, Analyze, Kickoff, Hunt, etc. — varies per profile)
- Press **F2** to switch profiles mid-session
- Press **F3** to load a different binary
- Press **F4** to set sudo password (for nmap/netexec privileged scans)
- Press **F5** to clear log; **F6** to stop session (resumable later)
- Type `/help` for all commands. `/status`, `/budget`, `/turns`, `/stop`, `/done`, `/info`, `/skills` are all available
- Adjusting `/budget` or `/turns` mid-session **preserves your conversation history** — they no longer reset state

### Backend selection

```sh
# Claude (default)
reverser i <target>

# LM Studio (auto-detects http://localhost:1234/v1)
reverser i <target> -b lmstudio -m qwen3.6-35b-a3b-ud-mlx

# Ollama (auto-detects http://localhost:11434/v1)
reverser i <target> -b ollama -m qwen3.5:35b-a3b-coding-nvfp4

# Any OpenAI-compatible endpoint
reverser i <target> -b local -m model-name --api-base http://gpu-server:8000/v1
```

Flags:

```
-b, --backend   Backend: claude (default), ollama, lmstudio, or any name for OpenAI-compatible
-m, --model     Model name/tag (required for non-claude backends)
--api-base      API base URL override
```

### Common options (all analysis commands)

```
-v              Show tool calls and results
-vv             Also show agent thinking
--budget N      Max API spend in USD (default: 2.0 autonomous, 5.0 interactive)
--log PATH      Custom session log path
--log-dir DIR   Custom log directory (default: ./logs)
```

---

## Profiles

15 profiles specialize the agent's system prompt, tool priorities, and available skills:

### Binary RE profiles

| Profile | Key | Description |
|---------|-----|-------------|
| General | `general` | Broad RE — works for any binary (default) |
| Linux Binary | `linux` | ELF-focused — syscalls, shared libs, debugging |
| Windows Binary | `windows` | PE-focused — DLL imports, .NET/COM, Windows API |
| Android APK | `android` | DEX, native libs, manifest, API endpoints |
| Chrome Extension | `chrome` | CRX/JS — manifest, permissions, data flow |
| Java / .NET | `managed` | JVM bytecode and .NET IL — decompilation, serialization |
| API Discovery | `api` | Document network APIs, endpoints, auth, request/response schemas |
| CTF / Crackme | `ctf` | Solve crackmes — find the flag/key/password |

### Network / web pentest profiles

| Profile | Key | Description |
|---------|-----|-------------|
| Pentest | `pentest` | Generic network pentest — recon, scanning, exploitation. **K=3** hypothesis-failure pivot rule. |
| Web Pentest | `webpentest` | Full OWASP web app pentest. **K=5** pivot rule. Playwright for SPA crawling + XSS confirmation. |
| Web API Pentest | `webapi` | REST/GraphQL API enumeration, auth flow abuse, mass assignment |
| Web Recon | `webrecon` | Perimeter footprinting — subdomain enum, port scan, tech fingerprinting, screenshot evidence |
| Active Directory | `ad` | Internal AD assumed-breach — NetExec, BloodHound, KB. 11 skills, hypothesis-driven discipline. |
| Exploit | `exploit` | Public-exploit hunter — searchsploit + msfvenom + Metasploit RPC bridge. Always-check-first exploitation. |
| Manager | `manager` | Network red-team conductor — plans hypotheses and dispatches specialists. **K=2** pivot rule. |

```sh
reverser interactive --list-profiles   # show all profiles and their skills
```

### Profile deep-dives

**Active Directory engagements (`ad`):** drives an assumed-breach internal AD methodology. 11 skills (initial recon → DC discovery → AS-REP/Kerberoast → cred validation → BloodHound graph → attack-path → NTDS dump → report). Backed by per-target persistent state in `targets/<target>/state.db`, NetExec for every relevant protocol, and BloodHound (Neo4j + bloodhound-python collector + 15 canned + free-form Cypher). Spray actions gated behind `REVERSER_AD_ALLOW_SPRAY=1`; optional `scope.toml` enforces in-scope CIDRs, no-DoS, no-account-lockout. The system prompt enforces a non-negotiable hypothesis loop ("Every 5 tool calls, write down (a) current hypothesis, (b) cheapest disconfirming experiment, (c) pivot. Don't grind past 3 failed attempts.").

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p ad 10.10.10.5
```

**Manager-led engagements (`manager`):** coordinates specialist sub-agents for network red-team work. Maintains a hypothesis tree in the per-target KB and dispatches the right specialty (`ad`, `pentest`, `webpentest`, `webapi`, `webrecon`, `exploit`) to test each hypothesis. The manager has a restricted tool surface — KB read/write, hypothesis CRUD, lightweight recon (`nmap_scan`, `dns_recon`, `whatweb_scan`), and `dispatch_specialist`. Heavy offensive tools require dispatch to a specialist. **Two-failure pivot rule (K=2)** is enforced via prompt; the dispatch wrapper appends a `## REQUIRED next action: call kb_update_hypothesis(...)` block to every result so local models don't forget to update state. `Status: partial` replaces `Status: error` when a specialist subprocess crashed but the report body has actionable findings.

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager 10.10.10.5
```

Skills: `k` Kickoff · `s` Status · `r` Report · `p` Pivot · `b` Budget · `w` Wrap up

For parallel dispatches (use cautiously):

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager 10.10.10.5 --max-parallel 3
```

**Exploit-hunter engagements (`exploit`):** wraps searchsploit, msfvenom, and the Metasploit RPC daemon into 8 MCP tools so the agent can close the "find a known exploit, generate a payload, try it" loop. `metasploit_run` is **always-check-first** by default — exploitation only fires when the module's `check` reports `vulnerable`, with `force=True` as an explicit escape hatch. A shared msfrpcd daemon runs at `127.0.0.1:55553` with per-target MSF workspaces; auth credentials persist at `<targets_root>/.shared/msfrpc/auth.json` (mode 0600). Confirmed exploits land as severity=high `FindingFact` entries; generated payloads land in `targets/<target>/loot/payloads/<name>-<sha8>.<ext>` as `ArtifactFact`s. Dispatchable from `manager`.

```sh
# Direct
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p exploit 10.10.10.5

# Or dispatched from manager (manager proposes CVE-related hypotheses, dispatches exploit specialist)
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager 10.10.10.5
```

Skills: `h` Hunt · `g` Generate payload · `t` Try exploit · `i` Handle session · `r` Report · `w` Wrap up

**Web pentest engagements (`webpentest`):** OWASP-flavored web app pentest with Playwright integration. SPA-aware crawling discovers JS-rendered routes that ffuf would miss. `web_browser_confirm_xss` injects a payload, installs a sentinel + dialog handler, and reports confirmed/refuted with screenshot evidence — far more reliable than "the payload string appears in the response body." Auto-screenshot evidence via `web_browser_capture_finding(finding_id, ...)` writes to `targets/<target>/findings/<id>/screenshot-<n>.png` and records an `ArtifactFact`. K=5 pivot rule discourages the "fuzz forever, confirm nothing" failure mode.

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p webpentest https://target.com
```

Skills: `r` Web Recon · `v` Vuln Scan · `d` Discover · `l` TLS/SSL · `q` SQLi Test · `m` Manual Test · `w` Report

---

## Sessions & persistence

Every interactive session creates a snapshot at `targets/<target>/sessions/<session_id>.json` with config, conversation, stats, and in-flight state. Snapshots autosave on every turn plus an emergency snapshot on TUI exit (atexit + SIGTERM handler).

```sh
# List all resumable sessions across all targets
reverser --list-sessions

# Resume the latest non-completed session for a target
reverser i 10.10.10.5 --resume

# Resume a specific session by ID
reverser i --resume 2026-05-12T22-54-46

# Take over a session whose process is still running
reverser i --resume <id> --force
```

Lifecycle states: `active` → `stopped` (resumable) / `completed` (terminal via `/done`) / `abandoned` (zero-turn TUI exit, auto-applied so empty launches don't clutter `--list-sessions`).

**Per-target knowledge base:** Every offensive tool writes to `targets/<target>/state.db` automatically. Inspect with `kb_show` inside the TUI, or:

```sh
sqlite3 targets/10.10.10.5/state.db ".tables"
sqlite3 targets/10.10.10.5/state.db "SELECT * FROM hypotheses;"
```

Findings, artifacts, credentials, and notes all persist between sessions on the same target. The hypothesis tree is the engagement plan and survives stop/resume.

**Bogus directory cleanup:** earlier versions of the CLI would create directories from malformed target arguments (URLs with schemes, free-text pastes, CIDRs with slashes). Run:

```sh
reverser --check-targets
```

…to list non-canonical target directories with `rm -rf` cleanup commands. The new CLI input validation (added in the manager-reliability bundle) prevents new bogus directories.

---

## Authorization & scope

Network-touching tools require explicit authorization:

```sh
# Per-shell
export REVERSER_PENTEST_AUTHORIZED=1

# Or per-project (lasts across shells)
touch .reverser-authorized
```

Without this, anything that touches the network errors with an authorization message. This is the harness's explicit acknowledgement that you have written authorization to test the target.

**Scope envelope** (optional, per-target): create `targets/<target>/scope.toml`:

```toml
[scope]
in_scope_cidrs = ["10.10.10.0/24", "192.168.1.0/24"]
out_of_scope_ips = ["10.10.10.99"]
allowed_hours = "09:00-17:00 UTC"
no_dos = true
no_account_lockout = true
```

Every `netexec_*`, `bloodhound_*`, `enum4linux_ng`, `metasploit_*`, `web_browser_*` tool consults this before acting. `no_account_lockout = true` hard-disables credential spray regardless of `REVERSER_AD_ALLOW_SPRAY`.

---

## Writeups

Every run logs to `logs/<binary>_<timestamp>.jsonl`. Generate a markdown writeup:

```sh
reverser writeup logs/boring_20260330_234443.jsonl
reverser writeup logs/boring_20260330_234443.jsonl -o writeup.md
```

For pentest engagements, `kb_export_report` (called from inside the TUI) renders `targets/<target>/report.md` with the full hypothesis tree, findings (severity-sorted), credentials, services, artifacts, and notes.

---

## Windows binaries

PE files (`.exe`, `.dll`) are supported via wine on Linux/macOS. The agent detects PE format automatically and uses wine for execution, strace, and GDB. Static analysis tools (radare2, strings, objdump) work natively on PE files.

---

## Harness (isolated container analysis)

For batch/CI use, the harness monitors an S3 bucket for binaries, runs analysis in isolated Incus containers with network restrictions (only Anthropic API access), and uploads results back to S3.

```sh
harness-init            # Set up Incus profile, firewall, and state DB
harness-build-image     # Build the reverser container base image
harness-test            # Verify container isolation
harness-run             # Start S3 monitoring loop
harness-process <file>  # Analyze a local binary in a container
harness-status          # Show processing statistics
harness-reset           # Clear state DB (--failed-only to reset failures)
harness-cleanup         # Destroy orphaned containers
```

Configuration lives in `harness.toml`. Requires AWS credentials and `ANTHROPIC_API_KEY` in `.env` or environment.

---

## Tools

91 MCP tools wired through a single MCP server (`mcp__re__*`). Grouped by capability:

| Category | Count | Tools |
|----------|------:|-------|
| Triage | 6 | `file_info`, `strings_search`, `checksec_binary`, `readelf_info`, `pe_info`, `binwalk_scan` |
| Static analysis | 5 | `r2_command`, `r2_decompile`, `objdump_disasm`, `nm_symbols`, `disassemble_bytes` |
| Decompilation | 2 | `procyon_decompile` (Java), `jadx_decompile` (Android) |
| Dynamic analysis | 3 | `run_binary`, `strace_run`, `gdb_batch` |
| Symbolic execution | 1 | `angr_find_paths` |
| Exploit dev | 1 | `rop_gadgets` |
| Network recon | 8 | `nmap_scan`, `dns_recon`, `nbtscan`, `smb_enum`, `ldap_search`, `kerberos_enum`, `enum4linux_ng`, `banner_grab` |
| Web scanning | 9 | `nikto_scan`, `gobuster_scan`, `ffuf_fuzz`, `nuclei_scan`, `sqlmap_test`, `subfinder_enum`, `wafw00f_detect`, `testssl_analyze`, `ssl_scan` |
| HTTP | 3 | `curl_request`, `http_request`, `whatweb_scan` / `whatweb_fingerprint` |
| NetExec | 6 | `netexec_smb` / `_winrm` / `_ldap` / `_mssql` / `_ssh` / `_ftp_wmi` |
| BloodHound | 6 | `bloodhound_start` / `_stop` / `_status` / `_collect` / `_canned` / `_query` |
| Metasploit bridge | 8 | `searchsploit_search`, `msfvenom_generate`, `metasploit_start` / `_stop` / `_status` / `_search` / `_run` / `_session` |
| Web browser (Playwright) | 14 | `web_browser_start` / `_status` / `_close`, `_navigate` / `_click` / `_type` / `_fill_form` / `_evaluate` / `_wait_for`, `_snapshot` / `_network_log`, `_capture_finding` / `_confirm_xss` / `_crawl` |
| Knowledge base | 11 | `kb_show`, `kb_list_hosts` / `_services` / `_creds`, `kb_add_note` / `_finding`, `kb_export_report`, `kb_add_hypothesis` / `_update_hypothesis` / `_get_hypothesis` / `_list_hypotheses` |
| Coordination | 1 | `dispatch_specialist` (manager profile only) |
| Filesystem | 4 | `list_directory`, `read_file`, `write_file`, `bash` |

---

## Project structure

```
src/reverser/
  cli.py              CLI entry point (triage, analyze, solve, interactive, writeup,
                      --list-sessions, --check-targets, --resume)
  agent.py            Agent orchestration (backend-agnostic)
  prompts.py          System prompt and analysis methodology
  sessions.py         Session snapshot persistence (save/load/list/resume)
  session_log.py      JSONL session logger
  writeup.py          Markdown writeup generator

  backends/
    base.py            Backend protocol (AgentEvent, Backend ABC)
    claude.py          Claude backend (via claude_agent_sdk)
    openai_compat.py   OpenAI-compatible backend (Ollama, LM Studio, vLLM, llama.cpp)
    tools.py           MCP-to-OpenAI tool conversion + execute_tool with allowlist

  profiles/
    __init__.py        Profile registry + dataclasses
    _skills.py         Shared skill constants
    general.py linux.py windows.py android.py chrome.py managed.py
    api.py ctf.py
    pentest.py webpentest.py webapi.py webrecon.py
    ad.py manager.py exploit.py

  kb/
    __init__.py        Public API (for_target, dataclasses, scope helpers)
    store.py           KB class + SQLite operations + hypothesis CRUD
    schema.py          DDL for hosts/services/credentials/findings/notes/
                       hypotheses/artifacts tables
    authz.py           Pentest authorization checks
    scope.py           scope.toml loader + assert_in_scope helpers
    parsers.py         Tool-output → KB-fact parsers

  tools/
    _common.py         Subprocess helpers (run_cmd, arun_cmd with target= for
                       circuit breaker), PE detection, pagination
    _conn_breaker.py   Per-target connection-failure circuit breaker
    triage.py          File identification and initial assessment
    static.py          Disassembly and decompilation
    dynamic.py         Runtime tracing and debugging
    python_analysis.py Symbolic execution and emulation
    exploit.py         ROP gadget search
    util.py            jadx, procyon, list_directory, read_file, write_file, bash
    network.py         Nmap, DNS, NetBIOS, SMB, LDAP, Kerberos, etc.
    web.py             Nuclei, sqlmap, ffuf, nikto, gobuster, testssl, etc.
    netexec.py         NetExec (nxc) wrappers — SMB/WinRM/LDAP/MSSQL/SSH/FTP+WMI
    bloodhound.py      Neo4j lifecycle + bloodhound-python collector + cypher
    enum4linux_ng.py   enum4linux-ng wrapper
    metasploit.py      Searchsploit + msfvenom + msfrpcd RPC bridge
    web_browser.py     Playwright integration (Chromium, singleton, screenshots)
    kb.py              KB MCP tool wrappers
    dispatch.py        dispatch_specialist (manager → sub-agent coordination)

  tui/
    app.py             Interactive TUI application (Textual)
    session.py         Multi-turn agent session manager
    modals/            F-key dialogs

  harness/
    cli.py             Harness CLI (init, monitor, process, status, etc.)
    config.py          TOML + env configuration loader
    monitor.py         S3 bucket polling and download
    pipeline.py        Analysis pipeline orchestration
    state.py           SQLite state tracker
    vm.py              Incus container lifecycle management

incus/
  build-image.sh       Container image builder
  setup-firewall.sh    nftables network isolation rules
  profile.yaml         Incus resource limits profile
  re-tools.nix         Ghidra/Rizin Nix composition

targets/                            Per-target persistent state
  <target>/
    state.db                          SQLite KB
    sessions/<id>.json                Resumable session snapshots
    findings/<id>/screenshot-<n>.png  Playwright-captured evidence
    loot/                             Dumps (NTDS, SAM, hashes)
    loot/payloads/                    msfvenom-generated payloads
    scope.toml                        Optional scope envelope

docs/superpowers/
  specs/    Design specs (one per major feature)
  plans/    Implementation plans (one per feature)

CAPABILITY_ROADMAP.md     Source-of-truth tracker for shipped + planned features
```

---

## Roadmap

See [`CAPABILITY_ROADMAP.md`](CAPABILITY_ROADMAP.md) for the full picture. All Top 5 items shipped:

| # | Capability | Status |
|---|---|---|
| 1 | Metasploit + msfvenom + searchsploit bridge | ✅ |
| 2 | Per-target persistent KB | ✅ |
| 3 | Playwright integration for webpentest | ✅ |
| 4 | NetExec + BloodHound + cypher | ✅ |
| 5 | Hypothesis-driven pentest/webpentest prompts | ✅ |

Next-tier candidates (in priority order): hashcat with rule-based cracking, coercion tooling (PetitPotam/ntlmrelayx), CVSS scoring + multi-format reports, `kb_diagnose` failure-analysis aggregator. See the roadmap doc for the full unstarted list.

---

## License

See `LICENSE` (or `pyproject.toml`).
