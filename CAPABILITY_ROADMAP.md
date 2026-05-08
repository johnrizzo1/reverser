# Reverser Capability Roadmap

Gap analysis of current reverser capabilities vs. an elite penetration tester's
toolkit (network, web, Windows/AD, cloud, wireless, mobile, OSINT). Items are
grouped by domain; the **Top 5** highest-ROI items are at the bottom.

Source context: gap analysis performed 2026-05-03 against the implementation on
`main` (44 wired tools, 11 profiles, Claude/OpenAI-compatible backends, Incus
harness). The HTB-style engagement captured in `pentest_report_10.13.38.23.md`
is referenced as a real-world failure case that motivates several items.

---

## Active Directory / Windows Internals
- [ ] BloodHound + SharpHound ingestion with cypher query interface (graph attack-path planning)
- [ ] Coercion tooling: PetitPotam, PrinterBug, DFSCoerce
- [ ] ntlmrelayx wrapper (impacket already installed)
- [ ] secretsdump wrapper
- [ ] lsassy wrapper
- [ ] DPAPI decryption helpers
- [ ] mimikatz output parsers
- [ ] hashcat with rule-based cracking pipeline (john alone is insufficient)
- [ ] Certipy (ADCS ESC1–ESC11 abuse)
- [ ] SCCM abuse tooling
- [ ] impacket-mssqlclient + linked-server hopping
- [ ] psexec / wmiexec / smbexec / dcomexec / atexec wrappers
- [ ] evil-winrm wrapper
- [ ] xfreerdp / RDP automation
- [ ] PassTheHash flow orchestration
- [ ] PowerView-equivalent enumeration / Group3r / ACL chain analysis
- [ ] PrivescCheck / winPEAS output parser
- [ ] Token impersonation, UAC bypass triage, persistence inventory

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

## Network Exploitation & Post-Exploitation
- [ ] Metasploit / msfconsole integration (db_nmap → search → check → exploit)
- [ ] msfvenom payload generation
- [ ] searchsploit + automated CVE → PoC fetch → adapt → run loop
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
- [ ] Multi-format export: PDF (weasyprint/pandoc), DOCX, SARIF/JSON
- [ ] Executive summary auto-generation from finding metadata
- [ ] Re-test / delta reports between engagements

## Cross-Cutting Improvements
- [ ] Vhost / Host-header fuzzing as a first-class step in pentest profile
- [ ] Tool composition macros (e.g., `ad_initial_foothold(target)` chains nmap → ldap_anon → kerb_enum → asreproast → kerberoast → john)
- [ ] Result cache / dedup keyed on (target, args) hash
- [ ] Target-specific wordlist generation: CeWL, dynamic expansion on hits, gotator/altdns permutations
- [ ] Failure analysis trigger: after K failed exploit attempts, force "stop, summarize, propose orthogonal directions"
- [ ] Per-target scope envelope (`scope.toml`: CIDR, port exclusions, hours, no-DoS) consulted before each tool call
- [ ] Evidence pipeline: auto-snapshot successful steps into `findings/<id>/` keyed to final report

---

## TOP 5 — Do These First

- [ ] **5. Restructure pentest/webpentest prompts around hypothesis → cheap experiment → update → pivot**, with explicit "stop spraying, propose three new attack surfaces" trigger after K failed exploitation attempts (the 10.13.38.23 report's failure mode)
- [x] **4. Build a per-target persistent KB** (`targets/<ip-or-host>/state.db` SQLite) tracking hosts, ports, services, credentials tried/worked, endpoints, findings — loaded automatically on every run, with a credential-lifecycle object fed to every new service
  - **Status (2026-05-04):** Shipped via Plans 1–2. Per-target SQLite KB with hosts/services/creds/findings/notes, 7 `kb_*` tools, and retrofitted writes across 10 network/web/AD tools. Scope envelope (`scope.toml`) added in Plan 5.
- [ ] **3. Wire Playwright (already in MCP) into the webpentest profile** — confirms XSS, handles SPAs, captures screenshot evidence
- [ ] **2. Add searchsploit + msfvenom + Metasploit RPC bridge** — closes the "find the public exploit and try it" loop
- [x] **1. Wrap NetExec (CME) + BloodHound/SharpHound + cypher queries** — closes ~60% of the AD gap in a weekend; biggest single ROI
  - **Status (2026-05-04):** Shipped via Plans 3–5. 6 `netexec_*` tools (smb/ldap/winrm/mssql/ssh/ftp+wmi), 6 `bloodhound_*` tools (lifecycle + collect + canned/cypher queries), and a dedicated `ad` profile with 11 skills.

> **Status (2026-05-04):** Top-5 items #1 and #4 shipped via Plans 1–5. See
> `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` and
> `docs/superpowers/plans/2026-05-03-plan-{1..5}-*.md`.
