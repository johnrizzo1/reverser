# Reverser Capability Roadmap

Gap analysis of current reverser capabilities vs. an elite penetration tester's
toolkit (network, web, Windows/AD, cloud, wireless, mobile, OSINT). Items are
grouped by domain; the **Top 5** highest-ROI items are at the bottom.

**Source-of-truth tracker.** Update this file when a checkbox-worthy item ships.
Status updates go inline as sub-bullets under the relevant item.

Source context: gap analysis performed 2026-05-03 against the implementation on
`main` (44 wired tools, 11 profiles, Claude/OpenAI-compatible backends, Incus
harness). The HTB-style engagement captured in `pentest_report_10.13.38.23.md`
is referenced as a real-world failure case that motivates several items.

**As of 2026-05-12:** 15 profiles registered, 77 MCP tools (75 unique), Claude
+ Ollama + LM Studio backends, per-target SQLite KB, session stop/resume,
manager profile (sub-agent coordination), exploit profile + msfrpc bridge,
hypothesis-driven discipline in pentest/webpentest, 540 passing tests.

---

## Recently Shipped (since 2026-05-04)

Major capabilities added after the initial roadmap was written. Each links to
its spec / plan under `docs/superpowers/`.

- **Exploit profile + Metasploit bridge** (2026-05-11 / -12) — closes Top 5 #1.
  8 new MCP tools wrapping `searchsploit`, `msfvenom`, and the `msfrpcd`
  RPC daemon: `searchsploit_search`, `msfvenom_generate`, and
  `metasploit_{start,stop,status,search,run,session}`. Shared msfrpcd daemon
  at `127.0.0.1:55553` with per-target MSF workspaces; auth at
  `<targets_root>/.shared/msfrpc/auth.json` (mode 0600); `fcntl.flock`
  serializes concurrent starts. `metasploit_run` is always-check-first
  with a `force=True` escape hatch; `scope.toml` enforced BEFORE the check
  fires; auto-`FindingFact(severity=high)` written on confirmed exploits;
  payloads land in `targets/<target>/loot/payloads/<name>-<sha8>.<ext>` as
  `ArtifactFact`s. New `exploit` profile joins the manager dispatch pool
  (5 → 6 specialties) with 6 skills (Hunt/Generate payload/Try exploit/
  Handle session/Report/Wrap up). Specs/plans:
  `2026-05-11-metasploit-bridge-{design,plan}.md`.
- **Manager profile + sub-agent dispatch** (2026-05-09 / -10) — `manager`
  profile coordinates specialist sub-agents (ad/pentest/webpentest/webapi/
  webrecon/exploit) via the SDK Task primitive. Maintains a hypothesis tree
  (`hypotheses` table in the per-target KB) with 4 CRUD tools + tree-rendering.
  17-tool allowlist enforced; specialists invoked via `dispatch_specialist`
  with per-dispatch budget caps. 6 manager skills (Kickoff/Status/Report/
  Pivot/Budget/Wrap up). Partially addresses Top-5 #5 for manager workflows.
  Specs/plans: `2026-05-09-manager-profile-{design,plan}.md`.
- **Stop & resume sessions** (2026-05-09 / -10) — multi-day engagements
  survive process exit / machine restart. New `reverser.sessions` module
  owns snapshots at `targets/<target>/sessions/<id>.json`. Lifecycle:
  active → stopped / completed / abandoned. CLI: top-level `--list-sessions`,
  `interactive --resume [SESSION_ID]`, `--force` for live takeover. TUI:
  F6 / `/stop`, `/done`, atexit + SIGTERM emergency snapshot, conversation
  replay on resume, header shows session info. Spec/plan:
  `2026-05-09-stop-resume-{design,plan}.md`.
- **LM Studio backend** (2026-05-10) — `-b lmstudio` shortcut alongside
  `-b ollama`. Same OpenAI-compatible backend, different default port (1234).
- **enum4linux-ng MCP wrapper** (2026-05-10) — `enum4linux_ng` tool with
  modes for all/quick/users/groups/shares/policy/os/ldap/netbios/kerberos/
  sessions/rid. KB writes for host/domain/notes. Binary install pending on
  macOS due to upstream samba/clang build issue; doc'd workaround in devenv.nix.
- **Additional offensive tools in devenv** (2026-05-10/11) — `ffuf`,
  `nuclei`, `subfinder`, `testssl`, `sqlmap`, `wafw00f`, `pycryptodome`
  (provides `Crypto` module for ldap3 NTLM), `requests`, `semgrep`.
- **Tool-allowlist plumbing** (2026-05-09) — `Profile.tools_allowlist`
  plumbed through TUI session / ClaudeBackend / OpenAICompatBackend. Used
  by the manager profile; defaults to wildcard for everything else.

---

## Active Directory / Windows Internals
- [x] BloodHound + SharpHound ingestion with cypher query interface (graph attack-path planning)
  - **Status (2026-05-04):** Shipped via Plan 4. 6 `bloodhound_*` tools: lifecycle (start/stop/status), collection (bloodhound-python collect), canned queries, raw cypher.
- [ ] Coercion tooling: PetitPotam, PrinterBug, DFSCoerce
- [ ] ntlmrelayx wrapper (impacket already installed)
- [ ] secretsdump wrapper
  - **Partial (2026-05-04):** `netexec_smb action=ntds` provides secretsdump-equivalent functionality via NetExec. Dedicated `impacket-secretsdump` wrapper not yet built.
- [ ] lsassy wrapper
- [ ] DPAPI decryption helpers
- [ ] mimikatz output parsers
- [ ] hashcat with rule-based cracking pipeline (john alone is insufficient)
- [ ] Certipy (ADCS ESC1–ESC11 abuse)
- [ ] SCCM abuse tooling
- [ ] impacket-mssqlclient + linked-server hopping
  - **Partial (2026-05-04):** `netexec_mssql` covers basic auth/exec; linked-server hopping not yet wrapped.
- [ ] psexec / wmiexec / smbexec / dcomexec / atexec wrappers
  - **Partial (2026-05-04):** `netexec_smb action=exec` covers smbexec/wmiexec patterns.
- [ ] evil-winrm wrapper
  - **Partial (2026-05-04):** `netexec_winrm` covers basic WinRM auth + exec.
- [ ] xfreerdp / RDP automation
- [ ] PassTheHash flow orchestration
  - **Partial (2026-05-04):** All `netexec_*` tools accept `nt_hash` arg and pull from KB credential lifecycle.
- [ ] PowerView-equivalent enumeration / Group3r / ACL chain analysis
  - **Partial (2026-05-04):** `bloodhound_query` + canned queries cover the graph side; PowerView-style live enumeration not yet wrapped.
- [ ] PrivescCheck / winPEAS output parser
- [ ] Token impersonation, UAC bypass triage, persistence inventory
- [x] SMB/RPC/LDAP/NetBIOS enumeration via enum4linux-ng
  - **Status (2026-05-10):** MCP wrapper shipped (`enum4linux_ng` tool). Binary install pending on macOS pending upstream samba fix; works on Linux.

## Web Application Pentest Depth
- [ ] Burp / ZAP proxy integration for replay & intercept
- [ ] Authenticated crawling for SPAs (wire up the already-available Playwright MCP)
- [ ] DOM XSS + client-side prototype pollution detection
- [ ] SSTI payload engine
- [ ] SSRF payload engine
- [ ] Insecure deserialization payload engine
- [ ] XXE payload engine
- [ ] CSTI payload engine
- [ ] jwt_tool wrapper
- [ ] OAuth attack toolkit
- [ ] GraphQL: graphql-cop, clairvoyance, InQL
- [ ] Cookie / session / CSRF analyzer tool
- [ ] HTTP request smuggling (smuggler.py, smuggling patterns)
- [ ] Browser-based XSS confirmation via Playwright
- [ ] feroxbuster (recursive intelligent fuzzing — would have helped on 10.13.38.23)
- [ ] kiterunner for API endpoint discovery
- [ ] Arjun / x8 parameter mining
- [x] ffuf for web fuzzing (paths, vhosts, params)
  - **Status (2026-05-10):** `ffuf_fuzz` MCP tool wired; binary installed via devenv.
- [x] nuclei template-based vuln scanning
  - **Status (2026-05-10):** `nuclei_scan` MCP tool wired; binary installed via devenv.
- [x] sqlmap SQL injection scanning
  - **Status (2026-05-10):** `sqlmap_test` MCP tool wired; binary installed via pip (devenv venv).
- [x] subfinder passive subdomain enumeration
  - **Status (2026-05-10):** `subfinder_enum` MCP tool wired; binary installed via devenv.
- [x] wafw00f WAF detection
  - **Status (2026-05-10):** `wafw00f_detect` MCP tool wired; installed via pip.

## Network Exploitation & Post-Exploitation
- [x] Metasploit / msfconsole integration (db_nmap → search → check → exploit)
  - **Status (2026-05-11):** Shipped as `metasploit_*` tools (start/stop/status/search/run/session). RPC-based; shared daemon + per-target workspace.
- [x] msfvenom payload generation
  - **Status (2026-05-11):** Shipped as `msfvenom_generate` tool; payloads land in `targets/<target>/loot/payloads/<name>-<sha8>.<ext>` with KB ArtifactFact.
- [x] searchsploit + automated CVE → PoC fetch → adapt → run loop
  - **Status (2026-05-11):** Shipped as `searchsploit_search` + `exploit` profile (6 skills: hunt/generate/try/handle session/report/wrap up).
- [ ] C2 listener handling: Sliver / Mythic / pwncat-cs / socat
- [ ] Pivoting: chisel / ligolo-ng / sshuttle wrappers
- [ ] Payload tooling: donut, sRDI, shellcode encoders

## Cloud
- [ ] AWS: Pacu, ScoutSuite, CloudFox, enumerate-iam, aws-cli wrapper with credential pivoting
- [ ] Azure / Entra ID: ROADtools, AADInternals, MicroBurst, AzureHound
- [ ] GCP: gcp_scanner, hayat
- [ ] Kubernetes: kube-hunter, kubeaudit, peirates
- [ ] Containers: trivy, grype, dive, dockle

## Wireless / RF / Hardware
- [ ] aircrack-ng suite
- [ ] hcxtools
- [ ] kismet
- [ ] bettercap
- [ ] hostapd-wpe
- [ ] wifite2
- [ ] Bluetooth: bluetoothctl, btlejack
- [ ] SDR: rtl_433, gqrx

## OSINT & Passive Recon
- [ ] ProjectDiscovery toolchain bundle: amass, assetfinder, httpx, naabu, dnsx
  - **Partial (2026-05-10):** `subfinder` shipped (one of the bundle). Rest pending.
- [ ] Shodan API wrapper
- [ ] Censys API wrapper
- [ ] FOFA API wrapper
- [ ] Hunter.io API wrapper
- [ ] Breach-data lookup: HIBP, dehashed (cred-spray seeds)
- [ ] GitHub/GitLab dorking: gitleaks, trufflehog, github-search
- [ ] Email/employee enumeration: theHarvester, hunter.io

## Mobile
- [ ] Frida / Objection instrumentation (Android + iOS)
- [ ] MobSF automated APK/IPA assessment
- [ ] iOS: class-dump, otool, IPA static analysis

## Reporting
- [ ] Per-finding CVSS + severity scoring with consistent template
- [ ] Evidence collection bundling (screenshot, request/response, command output)
  - **Partial (2026-05-04):** `targets/<target>/findings/` directory exists; `kb_add_finding` records metadata. Auto-snapshot of tool output is not yet wired (covered by Cross-Cutting "Evidence pipeline").
- [ ] Multi-format export: PDF (weasyprint/pandoc), DOCX, SARIF/JSON
  - **Partial (2026-05-04):** `kb_export_report` produces markdown with attack-tree section. PDF/DOCX/SARIF not yet.
- [ ] Executive summary auto-generation from finding metadata
- [ ] Re-test / delta reports between engagements

## Cross-Cutting Improvements
- [ ] Vhost / Host-header fuzzing as a first-class step in pentest profile
- [ ] Tool composition macros (e.g., `ad_initial_foothold(target)` chains nmap → ldap_anon → kerb_enum → asreproast → kerberoast → john)
  - **Partial (2026-05-09):** Manager profile achieves similar end via `dispatch_specialist` orchestration; macros for non-manager profiles still pending.
- [ ] Result cache / dedup keyed on (target, args) hash
- [ ] Target-specific wordlist generation: CeWL, dynamic expansion on hits, gotator/altdns permutations
- [x] Failure analysis trigger: after K failed exploit attempts, force "stop, summarize, propose orthogonal directions"
  - **Status (2026-05-12):** Shipped. After K failed exploit attempts against
    a hypothesis, the pentest (K=3) and webpentest (K=5) profile prompts now
    require mark-refuted + propose-three-orthogonal-surfaces. AD profile
    already had this discipline; manager profile achieves the same end via
    `dispatch_specialist`. Other profiles (general/linux/windows/etc.) are
    out of scope — they don't have an exploit-attempt surface.
- [x] Per-target scope envelope (`scope.toml`: CIDR, port exclusions, hours, no-DoS) consulted before each tool call
  - **Status (2026-05-04):** Shipped via Plan 5. Optional `targets/<target>/scope.toml` enforces in-scope CIDRs, no-DoS, no-account-lockout, allowed-hours; checked by every `netexec_*`, `bloodhound_*`, and `enum4linux_ng` tool.
- [ ] Evidence pipeline: auto-snapshot successful steps into `findings/<id>/` keyed to final report
- [x] Per-engagement persistence (stop & resume sessions)
  - **Status (2026-05-10):** Shipped. Snapshot per turn + on-exit; lifecycle states (active/stopped/completed/abandoned); CLI `--list-sessions` / `--resume [ID]`; conversation replay on resume.
- [x] Tool-allowlist enforcement per profile
  - **Status (2026-05-09):** Shipped. `Profile.tools_allowlist` plumbed through session/backends. Manager profile uses it to enforce delegation discipline.
- [x] Multi-backend support (Claude + Ollama + LM Studio + generic OpenAI-compat)
  - **Status (2026-05-10):** Shipped. `-b lmstudio` shortcut alongside `-b ollama`; arbitrary `--api-base` override for other OpenAI-compatible servers.

---

## TOP 5 — Priority Order (revised 2026-05-11)

Numbered in **execution order** — start with #1, then #2, etc. Original
numbering from 2026-05-03 in parentheses.

- [x] **1. (was #2) — Metasploit + msfvenom + searchsploit bridge.**
  - **Status (2026-05-11):** Shipped. 8 MCP tools: `searchsploit_search`,
    `msfvenom_generate`, `metasploit_{start,stop,status,search,run,session}`.
    Shared msfrpcd daemon + per-target MSF workspace; auth at
    `<targets_root>/.shared/msfrpc/auth.json` (0600); `metasploit_run`
    always-check-first with `force=True` escape hatch; scope.toml enforced
    BEFORE check fires; auto-finding written on successful exploit. New
    `exploit` profile joins the manager dispatch pool (5 → 6 specialties).
    Specs/plans: `2026-05-11-metasploit-bridge-design.md`,
    `2026-05-11-metasploit-bridge.md`.

- [x] **2. (was #4) — Per-target persistent KB.** ✅ shipped 2026-05-04.
  `targets/<ip-or-host>/state.db` SQLite tracking hosts/ports/services/
  credentials/findings/notes/hypotheses with credential-lifecycle object
  fed to every new service. See `2026-05-03-plan-{1,2}-*.md`.

- [ ] **3. (was #3) — Wire Playwright into the webpentest profile.**
  Playwright MCP is already available in the plugin context; needs profile
  wiring + tool helpers for SPA crawling, XSS confirmation, screenshot
  evidence capture. Modest implementation; big payoff for web evidence.

- [x] **4. (was #1) — NetExec + BloodHound + cypher.** ✅ shipped 2026-05-04
  via Plans 3–5. 6 `netexec_*` tools (smb/ldap/winrm/mssql/ssh/ftp+wmi),
  6 `bloodhound_*` tools (lifecycle + collect + canned/cypher), `ad` profile
  with 11 skills. ~60% of the AD gap closed.

- [x] **5. (was #5) — Hypothesis-driven pentest/webpentest prompts.**
  - **Status (2026-05-12):** Shipped via this work. Hypothesis-loop block
    inserted into `pentest` (K=3, every-5-calls) and `webpentest` (K=5,
    every-8-calls) system addenda. Skill-level reinforcement added to RECON,
    EXPLOIT, CREDS (pentest) and WEB_RECON, WEB_SQLI, WEB_MANUAL (webpentest).
    Specs/plans: `2026-05-12-hypothesis-driven-prompts-{design,plan}.md`.

> **Remaining work order:** #3 (items #1, #2, #4, and #5 already complete).

---

## Update protocol

- When a roadmap item ships, change `[ ]` to `[x]` and add an inline
  `**Status (YYYY-MM-DD):**` sub-bullet pointing to the spec/plan.
- Partials get a `**Partial (YYYY-MM-DD):**` sub-bullet describing what's
  covered and what's not.
- Major capabilities not on the roadmap (e.g. manager profile, stop/resume)
  go in the "Recently Shipped" section at the top.
- Top-5 reordering: keep the historical numbering in parentheses so the
  audit trail is preserved across reorderings.
