"""Agent profiles for specialized reverse engineering workflows."""

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A pre-packaged action the user can trigger."""
    name: str
    key: str          # short key for TUI (e.g. "t" for triage)
    description: str
    prompt: str       # injected as the user message


@dataclass
class Profile:
    """An agent profile that specializes behavior for a target type."""
    name: str
    key: str           # short identifier
    description: str
    system_addendum: str  # appended to the base system prompt
    skills: list[Skill] = field(default_factory=list)


# ── Shared skills ───────────────────────────────────────────────────

SKILL_TRIAGE = Skill(
    name="Triage",
    key="t",
    description="Quick file identification and security assessment",
    prompt="Perform a quick triage of the loaded binary. Run file_info, checksec, "
           "strings_search, and the appropriate header tool (readelf for ELF, pe_info "
           "for PE) in parallel. Summarize the results concisely.",
)

SKILL_ANALYZE = Skill(
    name="Full Analysis",
    key="a",
    description="Comprehensive static and dynamic analysis",
    prompt="Perform a full reverse engineering analysis of the loaded binary. Follow "
           "the standard methodology: triage, static analysis, targeted investigation, "
           "and report. Identify the binary's purpose, key functions, and any "
           "interesting behaviors or vulnerabilities.",
)

SKILL_SOLVE = Skill(
    name="Solve",
    key="s",
    description="Find the key/flag/password (crackme/CTF)",
    prompt="This binary is a crackme or CTF challenge. Find the correct input "
           "(key/flag/password) that the binary accepts. Start with triage and "
           "decompilation, try to solve analytically first, then use dynamic analysis "
           "or symbolic execution only if needed.",
)

SKILL_STRINGS = Skill(
    name="Strings",
    key="x",
    description="Search for interesting strings",
    prompt="Search the binary for interesting strings — URLs, API endpoints, error "
           "messages, keys, passwords, flags, file paths, format strings. Use "
           "strings_search with different minimum lengths and grep patterns. "
           "Categorize what you find.",
)

SKILL_DECOMPILE = Skill(
    name="Decompile",
    key="d",
    description="Decompile main function and key logic",
    prompt="List the binary's functions with r2_command aflj, then decompile main "
           "and any other interesting functions. Explain what the code does.",
)

SKILL_IMPORTS = Skill(
    name="Imports/Exports",
    key="i",
    description="Analyze imports, exports, and library usage",
    prompt="Analyze the binary's imports and exports. Use r2_command with iij for "
           "imports and iEj for exports. Identify which libraries and API functions "
           "the binary uses, and explain what capabilities they suggest.",
)

SKILL_API_MAP = Skill(
    name="API Map",
    key="m",
    description="Document network APIs and backend communication",
    prompt="Analyze the binary for network communication patterns. Look for:\n"
           "- URLs, hostnames, IP addresses in strings\n"
           "- HTTP method strings (GET, POST, PUT, DELETE)\n"
           "- API endpoint paths\n"
           "- JSON/protobuf/XML serialization\n"
           "- Authentication tokens, API keys, headers\n"
           "- WebSocket or gRPC usage\n"
           "- TLS/certificate handling\n\n"
           "Decompile functions that reference network APIs. Document each endpoint "
           "you find with its method, path, request/response format, and "
           "authentication mechanism. Present as a structured API reference.",
)

SKILL_WRITEUP = Skill(
    name="Writeup",
    key="w",
    description="Generate a structured report of findings so far",
    prompt="Based on everything we've discovered so far in this session, write a "
           "structured reverse engineering report covering:\n"
           "- Binary overview (type, architecture, purpose)\n"
           "- Security properties\n"
           "- Key functions and their roles\n"
           "- Notable findings (vulnerabilities, interesting behaviors, API endpoints)\n"
           "- Solution (if applicable)\n"
           "Use markdown formatting.",
)

SKILL_RUN = Skill(
    name="Run",
    key="r",
    description="Execute the binary and observe behavior",
    prompt="Run the binary and observe its behavior. Try it with no arguments first, "
           "then with common test inputs. Report what it prints, what it expects, "
           "and any notable behavior.",
)

# ── Pentest-specific skills ────────────────────────────────────────

SKILL_RECON = Skill(
    name="Recon",
    key="r",
    description="Full network reconnaissance of the target",
    prompt="Perform comprehensive reconnaissance of the target. Run these in parallel:\n"
           "1. nmap_scan with version detection (scan_type='version')\n"
           "2. dns_recon to enumerate DNS records\n"
           "3. If web ports are likely, run whatweb_scan\n\n"
           "Summarize all open ports, services, versions, and OS fingerprinting results.",
)

SKILL_PORTSCAN = Skill(
    name="Port Scan",
    key="p",
    description="Thorough port scan of the target",
    prompt="Run a thorough port scan of the target:\n"
           "1. First scan top 1000 TCP ports with nmap_scan (scan_type='tcp')\n"
           "2. If interesting services found, do version detection on open ports\n"
           "3. Consider a UDP scan on common ports (53, 67, 69, 123, 161, 500)\n\n"
           "Report all discovered ports, services, and versions.",
)

SKILL_WEBSCAN = Skill(
    name="Web Scan",
    key="w",
    description="Web application vulnerability scanning",
    prompt="Perform web application scanning on the target:\n"
           "1. Run curl_request to grab headers and identify the server\n"
           "2. Run whatweb_scan to fingerprint web technologies\n"
           "3. Run nikto_scan for known vulnerabilities\n"
           "4. Run gobuster_scan to discover hidden directories and files\n"
           "5. Check for common paths: /robots.txt, /sitemap.xml, /.git, /admin, /api\n\n"
           "Report all findings including server software, technologies, "
           "vulnerabilities, and discovered paths.",
)

SKILL_SSLCHECK = Skill(
    name="SSL/TLS",
    key="l",
    description="Analyze SSL/TLS configuration and certificates",
    prompt="Analyze the SSL/TLS configuration of the target:\n"
           "1. Run ssl_scan to check certificate details and cipher suites\n"
           "2. Check for common TLS misconfigurations\n"
           "3. Verify certificate validity, issuer, and expiration\n\n"
           "Report any weak ciphers, expired certs, or protocol issues.",
)

SKILL_ENUM = Skill(
    name="Enumerate",
    key="e",
    description="Service-specific enumeration based on discovered ports",
    prompt="Based on previously discovered services, perform targeted enumeration:\n"
           "- HTTP/HTTPS: Use curl_request to probe endpoints, check headers\n"
           "- SSH: Grab banner with banner_grab\n"
           "- FTP: Check for anonymous login with banner_grab\n"
           "- SMB (139/445): Use smb_enum tool for shares and nmap SMB scripts\n"
           "- LDAP (389/636/3268): Use ldap_search with anonymous bind, then enumerate users/groups/SPNs\n"
           "- Kerberos (88): Use kerberos_enum for user enumeration\n"
           "- SMTP: Grab banner and check for VRFY with banner_grab\n"
           "- DNS: Run dns_recon with zone transfer check\n"
           "- MySQL/PostgreSQL: Grab banner\n\n"
           "Focus on services that were found open. Report versions, "
           "configurations, and potential attack vectors.",
)

SKILL_VULNSCAN = Skill(
    name="Vuln Scan",
    key="v",
    description="Run vulnerability scanning scripts",
    prompt="Run targeted vulnerability scans on the target:\n"
           "1. Run nmap_scan with scripts='vuln' against discovered open ports\n"
           "2. For web servers, run nikto_scan with broader tuning\n"
           "3. Check for known CVEs based on discovered service versions\n\n"
           "Report all identified vulnerabilities with severity and potential impact.",
)

SKILL_PENTEST_WRITEUP = Skill(
    name="Writeup",
    key="u",
    description="Generate a penetration test report of findings",
    prompt="Based on everything discovered so far, write a structured "
           "penetration test report covering:\n"
           "- Executive summary\n"
           "- Target information and scope\n"
           "- Discovered hosts, ports, and services\n"
           "- Identified vulnerabilities (with severity)\n"
           "- Web application findings\n"
           "- SSL/TLS assessment\n"
           "- Recommendations for remediation\n"
           "Use markdown formatting.",
)

# ── AD-specific skills ─────────────────────────────────────────────

SKILL_AD_INITIAL_RECON = Skill(
    name="Initial recon",
    key="r",
    description="nmap top-1000 + smb_enum + ldap_search anon + nbtscan",
    prompt="Confirm the target IP, domain (if known), and engagement window. Then run, "
           "in parallel: nmap_scan with version detection on top-1000 TCP ports, smb_enum "
           "for shares and SMB security mode, ldap_search with anonymous bind for the root "
           "DSE and any naming contexts, and nbtscan_scan to harvest NetBIOS names and "
           "workgroup. Record everything into the KB (parsers do this automatically). "
           "Then call kb_show to see the merged picture.",
)

SKILL_AD_IDENTIFY_DCS = Skill(
    name="Identify DCs",
    key="d",
    description="kerberos_enum userenum + ldap_search for objectClass=domainDNS",
    prompt="Identify Domain Controllers on the target. Run kerberos_enum with action=userenum "
           "(uses nmap krb5-enum-users) and ldap_search with filter '(objectClass=domainDNS)' "
           "and '(&(objectCategory=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))'. "
           "Mark any matched hosts as DCs in the KB via kb_add_note (the LDAP parser also flags "
           "is_dc=True automatically when it sees the SERVER_TRUST_ACCOUNT bit). Call kb_list_hosts "
           "to confirm the result.",
)

SKILL_AD_SPRAY = Skill(
    name="Spray known wordlist",
    key="s",
    description="netexec_smb spray (gated by REVERSER_AD_ALLOW_SPRAY)",
    prompt="Run a credential spray against SMB. First call kb_list_creds to see what we already "
           "have — do not re-spray credentials we have already validated or invalidated. Then run "
           "netexec_smb with action=spray, a small username list, and 1–3 known-bad passwords "
           "(e.g. 'Welcome1', 'Password1', '<Domain>2026!'). The tool refuses unless "
           "REVERSER_AD_ALLOW_SPRAY=1 is set; if it is unset, stop and explain to the user why. "
           "REVERSER_SPRAY_MAX caps attempts per user (default 3). Record any new validated cred "
           "via the standard KB write path (the tool does this for you).",
)

SKILL_AD_ASREP = Skill(
    name="AS-REP roast",
    key="a",
    description="kerberos_enum asreproast (anon LDAP for userlist)",
    prompt="Hunt for AS-REP-roastable accounts. First, if no userlist is present, run "
           "ldap_search with anonymous bind and filter "
           "'(&(samAccountType=805306368)(userAccountControl:1.2.840.113556.1.4.803:=4194304))' "
           "to find users with DONT_REQ_PREAUTH. Save that list, then run kerberos_enum with "
           "action=asreproast against the DC. Each returned hash is recorded in the KB as a "
           "credential with kerberos_ticket=<hash> and status=untested, plus an artifact under "
           "loot/. Do NOT crack hashes inside this tool — surface them and tell the user to crack "
           "with hashcat -m 18200 offline.",
)

SKILL_AD_KERBEROAST = Skill(
    name="Kerberoast",
    key="k",
    description="kerberos_enum kerberoast with KB-stored creds",
    prompt="Request TGS tickets for all SPN-bearing accounts. First call kb_list_creds status='valid' "
           "to find a usable domain credential. Then run kerberos_enum with action=kerberoast, passing "
           "the validated cred. Each returned TGS hash is recorded as a credential row with "
           "kerberos_ticket and status=untested, plus an artifact under loot/. Tell the user to crack "
           "offline with hashcat -m 13100. If no valid cred exists yet, fall back to the AS-REP skill first.",
)

SKILL_AD_VALIDATE_CREDS = Skill(
    name="Validate creds everywhere",
    key="v",
    description="netexec_*/check_auth across all KB creds",
    prompt="For every credential in the KB with status in ('untested', 'valid'), test it against "
           "every relevant service we have discovered. First call kb_list_creds and kb_list_services. "
           "Then for each (cred, host:port) pair, dispatch the right netexec_* tool with action=check_auth: "
           "445/tcp → netexec_smb, 5985/5986 → netexec_winrm, 389/636 → netexec_ldap, "
           "1433 → netexec_mssql, 22 → netexec_ssh, 21 → netexec_ftp_wmi (protocol='ftp'). "
           "Run independent checks in parallel. The tools record cred_results into the KB automatically. "
           "When you find a new valid cred, immediately move on to the BloodHound skill from that user.",
)

SKILL_AD_BLOODHOUND_COLLECT = Skill(
    name="Collect BloodHound",
    key="c",
    description="bloodhound_start → collect → status",
    prompt="Stand up the BloodHound graph for this target. Sequence: "
           "1. bloodhound_start(target) — boots Neo4j with data dir under targets/<target>/neo4j/. "
           "2. bloodhound_collect(target, domain, dc_ip, username, password|nt_hash, "
           "collection_methods='Default,LoggedOn'). For stealthier runs use 'DCOnly'. "
           "3. bloodhound_status(target) — confirm the imported counts (Users, Computers, Groups, OUs, GPOs). "
           "If counts are zero, the collector failed silently — re-check creds and DC reachability.",
)

SKILL_AD_FIND_PATHS = Skill(
    name="Find attack paths",
    key="p",
    description="bloodhound_canned shortest_path_to_da, owned_to_high_value",
    prompt="Map our path to Domain Admin. Run bloodhound_canned with query_name='shortest_path_to_da' "
           "first — it shows the cheapest existing path. Then run query_name='owned_to_high_value' "
           "with params={'username': '<owned-user>@<DOMAIN>'} for each currently-validated user. "
           "Also run 'kerberoastable_users' and 'unconstrained_delegation' to surface fresh primitives. "
           "If the canned queries do not answer the question, drop to bloodhound_query with a custom "
           "Cypher snippet (read-only by default). Record promising paths via kb_add_note.",
)

SKILL_AD_DUMP_SECRETS = Skill(
    name="Dump secrets",
    key="m",
    description="netexec_smb sam/lsa/ntds with valid local-admin",
    prompt="Once we have local-admin (or DA equivalent) on a host, dump cached secrets. "
           "Sequence per target host with valid admin cred: "
           "1. netexec_smb action='sam' — local SAM hashes. "
           "2. netexec_smb action='lsa' — LSA secrets and DPAPI keys. "
           "3. netexec_smb action='ntds' — only on a DC; dumps the entire NTDS.dit. This is loud. "
           "Each dump is auto-saved under loot/ and per-hash credentials are recorded as untested. "
           "Confirm with kb_list_creds afterwards. Do NOT crack inside the tool — surface to the user.",
)

SKILL_AD_SHOW = Skill(
    name="Show what we know",
    key="w",
    description="kb_show + kb_list_creds + kb_list_hosts",
    prompt="Stop. Before the next attack, dump everything we have learned so far. Call, in parallel: "
           "kb_show (single-screen overview), kb_list_hosts (full host inventory), and "
           "kb_list_creds (every credential with status). Read the output carefully. State, in two "
           "sentences: (a) the current best hypothesis for the path to DA, (b) the cheapest experiment "
           "that would disconfirm it. Then resume.",
)

SKILL_AD_REPORT = Skill(
    name="Generate report",
    key="g",
    description="kb_export_report",
    prompt="Generate the engagement report. Call kb_export_report(target) — it renders "
           "targets/<target>/report.md from the KB contents (hosts, services, creds, findings, "
           "notes, artifacts) in the same style as pentest_report_10.13.38.23.md. Read the file "
           "back and confirm the executive summary, methodology, and findings are accurate. If "
           "any finding is missing, add it via kb_add_finding and re-run the report.",
)

SKILL_SYSCALLS = Skill(
    name="Syscalls",
    key="y",
    description="Trace system calls during execution",
    prompt="Run the binary under strace to observe its syscall behavior. Focus on "
           "file operations, network calls, and process manipulation. Summarize "
           "the syscall patterns.",
)

SKILL_EXPLOIT = Skill(
    name="Exploit",
    key="x",
    description="Search for and attempt known exploits against discovered services",
    prompt="Based on previously discovered service versions, attempt exploitation:\n"
           "1. Run `searchsploit <service> <version>` via bash for each discovered service\n"
           "2. Prioritize unauthenticated remote exploits with critical/high severity\n"
           "3. For promising exploits, review the exploit code, stage it, and attempt execution via bash\n"
           "4. Check Metasploit modules with `msfconsole -q -x 'search <service>'` if available\n"
           "5. Document each attempt: exploit used, target, outcome, and any obtained access\n\n"
           "Focus on confirmed vulnerabilities from the vuln scan phase first. "
           "Do not attempt DoS or destructive exploits.",
)

SKILL_CREDS = Skill(
    name="Cred Attack",
    key="c",
    description="Credential attacks: brute force, default creds, hash cracking",
    prompt="Perform credential attacks against discovered services:\n"
           "1. Test default credentials on all services (admin/admin, root/root, service-specific defaults)\n"
           "2. For SSH, FTP, SMB, HTTP Basic — run `hydra -L top-usernames-shortlist.txt -P rockyou.txt "
           "<target> <service>` via bash (use small wordlists first)\n"
           "3. For Kerberos: use kerberos_enum for AS-REP roasting and kerberoasting\n"
           "4. If hashes were obtained, attempt cracking with `hashcat` or `john` via bash\n"
           "5. Test password reuse across all discovered services\n\n"
           "Document all valid credentials found and the services they grant access to.",
)


# ── Common skill sets ───────────────────────────────────────────────

_CORE_SKILLS = [
    SKILL_TRIAGE, SKILL_ANALYZE, SKILL_STRINGS, SKILL_DECOMPILE,
    SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
]

_SOLVE_SKILLS = [
    SKILL_TRIAGE, SKILL_SOLVE, SKILL_DECOMPILE, SKILL_STRINGS,
    SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
]

_API_SKILLS = [
    SKILL_TRIAGE, SKILL_ANALYZE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
    SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
]

_PENTEST_SKILLS = [
    SKILL_RECON, SKILL_PORTSCAN, SKILL_WEBSCAN, SKILL_SSLCHECK,
    SKILL_ENUM, SKILL_VULNSCAN, SKILL_EXPLOIT, SKILL_CREDS, SKILL_PENTEST_WRITEUP,
]


# ── Profile definitions ────────────────────────────────────────────

PROFILES: dict[str, Profile] = {}


def _register(p: Profile):
    PROFILES[p.key] = p
    return p


_register(Profile(
    name="General",
    key="general",
    description="Broad reverse engineering — works for any binary type",
    system_addendum="",
    skills=_CORE_SKILLS,
))

_register(Profile(
    name="Linux Binary",
    key="linux",
    description="ELF binaries — syscall analysis, shared libraries, debugging",
    system_addendum="""\

## Profile: Linux ELF Binary Analysis

You are analyzing a Linux ELF binary. Prioritize:
- ELF-specific tools: `readelf_info` for headers/sections/symbols, `checksec_binary` for RELRO/NX/PIE/canary
- Shared library analysis: `r2_command` with `iij` for imported functions, identify glibc/system call usage
- Dynamic analysis with `strace_run` (filter by category: file, network, process, memory)
- `gdb_batch` for breakpoint-based investigation
- Focus on the Linux-specific patterns: signal handlers, ptrace anti-debug, LD_PRELOAD hooks, /proc filesystem access
- For privilege escalation analysis, check for setuid bits, capabilities, and dangerous syscalls
""",
    skills=_CORE_SKILLS,
))

_register(Profile(
    name="Windows Binary",
    key="windows",
    description="PE executables — DLL imports, .NET/COM, Windows API",
    system_addendum="""\

## Profile: Windows PE Binary Analysis

You are analyzing a Windows PE binary. Prioritize:
- `pe_info` for PE headers, sections, imports, exports, and security features (ASLR, DEP, CFG, SafeSEH)
- Identify the subsystem: console, GUI, native, driver
- DLL import analysis is critical — map the capabilities by DLL:
  - kernel32.dll: process/thread/file management
  - user32.dll: GUI and window management
  - advapi32.dll: registry, security, crypto
  - ws2_32.dll / wininet.dll / winhttp.dll: networking
  - ntdll.dll: low-level NT API, possible syscall evasion
  - crypt32.dll / bcrypt.dll: cryptography
- Check for .NET metadata (`_CorExeMain` import, CLI header) — if found, focus on IL decompilation
- Check for COM/OLE usage (CoCreateInstance, CLSIDFromString)
- For packed binaries: check section names (.UPX, .aspack, .themida), high entropy sections
- Dynamic analysis uses wine automatically — `run_binary`, `strace_run`, `gdb_batch` all handle this
- Anti-analysis: check for IsDebuggerPresent, NtQueryInformationProcess, timing checks
""",
    skills=_CORE_SKILLS,
))

_register(Profile(
    name="Android APK",
    key="android",
    description="APK/XAPK analysis — manifest, DEX, native libs, API endpoints",
    system_addendum="""\

## Profile: Android APK/XAPK Analysis

You are analyzing an Android application package. Key approach:
- First use `file_info` and `binwalk_scan` to identify the package structure
- Use `strings_search` extensively to find:
  - API endpoints, URLs, hostnames
  - Package names, activity names, content provider URIs
  - Firebase/cloud service references
  - Hardcoded keys, tokens, secrets
- For DEX files within the APK, use `strings_search` and static analysis tools
- Look for native libraries (.so files) in lib/ directories — analyze these as ELF binaries
- Focus on:
  - AndroidManifest.xml: permissions, components, intent filters
  - API communication: HTTP clients (OkHttp, Retrofit, Volley), WebSocket, gRPC
  - Authentication: OAuth, JWT, API keys, certificate pinning
  - Data storage: SharedPreferences, SQLite, encrypted storage
  - Third-party SDKs: analytics, ads, crash reporting
  - Obfuscation: ProGuard/R8 name mangling, string encryption, class loading tricks
- When the binary is a .so native library, do full ELF analysis with JNI function identification
""",
    skills=_API_SKILLS,
))

_register(Profile(
    name="Chrome Extension",
    key="chrome",
    description="CRX/ZIP browser extensions — manifest, JS, permissions, API hooks",
    system_addendum="""\

## Profile: Chrome Browser Extension Analysis

You are analyzing a Chrome browser extension (CRX or extracted ZIP). Key approach:
- Use `binwalk_scan` and `file_info` to identify the archive structure
- Use `strings_search` extensively across all files to find:
  - API endpoints and external service URLs
  - Chrome extension API usage (chrome.*, browser.*)
  - Content Security Policy directives
  - OAuth client IDs, API keys, tokens
  - WebRequest/WebNavigation hooks (traffic interception)
- Focus on these critical areas:
  - **manifest.json**: permissions, content_security_policy, background scripts, content scripts
  - **Permissions analysis**: activeTab, tabs, webRequest, webRequestBlocking, cookies, storage, <all_urls>
  - **Background/service worker**: main extension logic, message passing, alarm handlers
  - **Content scripts**: DOM manipulation, page injection, data exfiltration vectors
  - **Native messaging**: communication with host applications
  - **Web accessible resources**: pages/scripts exposed to web content
- Security concerns:
  - Overprivileged permissions
  - eval() or Function() usage
  - External script loading (CDN, remote code execution)
  - Data exfiltration to third-party servers
  - XSS vectors in extension pages
  - Message passing without origin validation
- Document the extension's data flow: what it reads from pages, what it sends externally
""",
    skills=_API_SKILLS,
))

_register(Profile(
    name="Java / .NET",
    key="managed",
    description="JVM bytecode (JAR/class) and .NET assemblies (DLL/EXE with IL)",
    system_addendum="""\

## Profile: Java / .NET Managed Code Analysis

You are analyzing managed code — either JVM bytecode or .NET IL. Key approach:
- First determine the type: use `file_info` and check for:
  - Java: JAR files (ZIP with META-INF/MANIFEST.MF), .class files (0xCAFEBABE magic)
  - .NET: PE with _CorExeMain import, CLI header, mscoree.dll dependency
- Use `strings_search` extensively — managed code is rich in string literals:
  - Class names, method names, package/namespace names
  - SQL queries, connection strings
  - API endpoints, URLs
  - Reflection targets, serialization hints
  - Resource file references
- For Java:
  - `binwalk_scan` to list JAR contents
  - Look for obfuscation: single-letter class/method names, string encryption, class loading tricks
  - Identify frameworks: Spring, Hibernate, Apache libraries
  - Check for deserialization gadgets (ObjectInputStream, XMLDecoder)
- For .NET:
  - `pe_info` for the PE structure and imports
  - Look for P/Invoke declarations (native interop)
  - Identify framework usage: ASP.NET, WCF, Entity Framework
  - Check for reflection-based calls, dynamic assembly loading
- Security focus:
  - Hardcoded credentials and connection strings
  - Insecure deserialization
  - SQL injection patterns
  - Cryptographic key material
  - Debug/trace code left in production
""",
    skills=_API_SKILLS,
))

_register(Profile(
    name="API Discovery",
    key="api",
    description="Focus on documenting network APIs, endpoints, and backend communication",
    system_addendum="""\

## Profile: API Discovery and Documentation

Your primary goal is to document the API surface between this software and its backend services. \
This is NOT a CTF — do not try to crack or bypass anything. Instead, produce a thorough API reference.

Focus areas:
1. **Endpoint Discovery**: Find all URLs, hostnames, API paths in strings and code
2. **Request/Response Format**: Identify HTTP methods, content types, serialization (JSON, protobuf, XML, msgpack)
3. **Authentication**: Document auth mechanisms — OAuth, JWT, API keys, session tokens, mTLS, HMAC signatures
4. **Data Models**: Identify request/response schemas from serialization code
5. **Error Handling**: Find error codes, retry logic, fallback endpoints
6. **Rate Limiting**: Look for throttling, backoff, or quota logic
7. **WebSocket/Streaming**: Identify real-time communication channels and message formats
8. **Certificate Pinning**: Document TLS configuration and pinned certificates

Output format: Produce a structured API reference with:
- Base URL(s) and environment detection (prod/staging/dev)
- For each endpoint: method, path, auth required, request schema, response schema, error codes
- Authentication flow diagram (in text)
- Notable headers (User-Agent, custom headers, API versioning)

Analysis approach:
- Start with `strings_search` for URLs and API paths
- Decompile network-related functions
- Trace `r2_command` cross-references from URL strings to calling functions
- Map the request construction flow from parameters → serialization → HTTP call
- Use `strace_run` with network category to observe actual network calls if possible
""",
    skills=_API_SKILLS,
))

_register(Profile(
    name="Pentest",
    key="pentest",
    description="Network penetration testing — target an IP for recon, scanning, and exploitation",
    system_addendum="""\

## Profile: Network Penetration Testing

You are performing an authorized penetration test against a network target (IP address or hostname). \
This is NOT binary analysis — you are testing a live network host.

**Target identification is your starting point.** Use network tools, not binary analysis tools.

Methodology (follow in order):
1. **Reconnaissance**: Start with nmap_scan (version detection) and dns_recon in parallel. \
   Use whatweb_scan if web ports are suspected.
2. **Port Scanning**: Thorough TCP scan, then targeted UDP on common ports. \
   Always do version detection on discovered open ports. \
   nmap_scan automatically uses sudo for any operation requiring root.
3. **Service Enumeration**: For each discovered service, perform targeted enumeration:
   - HTTP/HTTPS: curl_request for headers, whatweb_scan for tech stack, gobuster_scan for directories
   - SSL/TLS: ssl_scan for certificate and cipher analysis
   - SSH/FTP/SMTP/etc: banner_grab for version info
   - SMB/NetBIOS: smb_enum tool for share listing and nmap SMB scripts. \
     Also nbtscan for NetBIOS names and workgroups.
   - LDAP/Active Directory: ldap_search (uses Python ldap3) for users, groups, computers, SPNs, GPOs. \
     Start with anonymous bind to check if unauthenticated queries work. \
     Enumerate: (objectClass=user), (objectClass=computer), (servicePrincipalName=*)
   - Kerberos: kerberos_enum for user enumeration (userenum), AS-REP roasting (asreproast), \
     and kerberoasting (kerberoast). Uses nmap krb5-enum-users and impacket.
   - DNS: Zone transfer attempts, record enumeration
4. **Vulnerability Scanning**: nmap_scan with scripts='vuln', nikto_scan for web servers
5. **Web Application Testing**: Directory brute-force, parameter fuzzing, header analysis
6. **Reporting**: Structured findings with severity ratings

**Nmap NSE script names — ONLY use these exact names (do NOT invent names):**
- SMB: smb-enum-shares, smb-enum-users, smb-os-discovery, smb-security-mode, smb2-security-mode
- Kerberos: krb5-enum-users (NOT kerberos-enum-users, NOT krb5-enum-accounts)
- LDAP: ldap-rootdse, ldap-search (NOT ldap-novell-get-current-time)
- HTTP: http-enum, http-title, http-headers, http-methods, http-robots.txt
- SSL/TLS: ssl-enum-ciphers, ssl-cert
- DNS: dns-zone-transfer, dns-brute
- FTP: ftp-anon, ftp-bounce
- SMTP: smtp-commands, smtp-enum-users
- Vuln: vuln (category — runs all vuln scripts)

**Wordlists**: Seclists is installed. Use shortcut names in gobuster_scan and kerberos_enum: \
'common.txt', 'big.txt', 'top-usernames-shortlist.txt', 'xato-net-10-million-usernames.txt'. \
Or use paths relative to seclists (e.g. 'Discovery/Web-Content/common.txt').

Tool speed tiers for network targets:
- **Tier 1 (Fast, <5s)**: curl_request, banner_grab, dns_recon, nbtscan — use freely, in parallel
- **Tier 2 (Moderate, 5-30s)**: nmap_scan (targeted ports), whatweb_scan, ssl_scan, ldap_search, smb_enum — use liberally
- **Tier 3 (Slow, 30-180s)**: nmap_scan (full), nikto_scan, gobuster_scan, kerberos_enum — be targeted

**Privilege handling**: nmap_scan automatically uses sudo for all operations requiring \
root (SYN/UDP scans, version detection, OS detection, NSE scripts). The user sets their \
sudo password via /sudo or F4 in the TUI.

**CRITICAL RULES:**
- If a tool returns "command not found", do NOT retry it. Move on to an alternative approach.
- Do NOT try to install packages (no apt, pip, etc.) — the environment is managed by devenv/nix.
- Do NOT invent nmap script names. Only use the exact names listed above.
- Always use the bash tool for commands not covered by the specialized tools.

IMPORTANT: This is authorized penetration testing. Focus on discovery and enumeration. \
Do not attempt destructive attacks or denial of service.
""",
    skills=_PENTEST_SKILLS,
))

_register(Profile(
    name="CTF / Crackme",
    key="ctf",
    description="Crackme and CTF challenges — find the flag/key/password",
    system_addendum="""\

## Profile: CTF / Crackme Challenge

Your sole goal is to find the correct input that the binary accepts — the flag, key, or password. \
Speed and efficiency are critical. Do not write reports or explain methodology — just solve it.

Strategy:
1. Triage fast (parallel: file_info, checksec, strings, readelf/pe_info)
2. Look for the flag in strings first — many CTFs hide it in plain sight
3. Decompile the validation function — most crackmes have one
4. Try to solve analytically: XOR ciphers, simple comparisons, math equations
5. If analytical fails, use dynamic analysis to observe the validation
6. angr_find_paths is the nuclear option — use only when you've identified exact addresses
7. Once you have the answer, verify by running the binary with that input

Common patterns:
- strcmp/strncmp against hardcoded or derived strings
- Character-by-character XOR/ADD/SUB transformations
- Hash comparisons (MD5, SHA1, CRC32)
- Anti-debugging: ptrace, timing checks, /proc/self/status
- Multi-stage: first input unlocks second stage
- Encoded flags: base64, hex, custom encoding
""",
    skills=_SOLVE_SKILLS,
))


# ── Web pentest skills ─────────────────────────────────────────────

SKILL_WEB_RECON = Skill(
    name="Web Recon",
    key="r",
    description="Reconnaissance: subdomain enum, port scan, fingerprinting, WAF detection",
    prompt="Perform web reconnaissance on the target. Run in parallel: nmap_scan for open "
           "ports, subfinder_enum for subdomains, whatweb_fingerprint for technology stack, "
           "and wafw00f_detect for WAF detection. Summarize the attack surface.",
)

SKILL_WEB_SCAN = Skill(
    name="Vuln Scan",
    key="v",
    description="Run vulnerability scanners (Nuclei, Nikto) against the target",
    prompt="Run vulnerability scanners against the target. Use nuclei_scan with "
           "severity=critical,high first, then nikto_scan. Summarize all findings by severity.",
)

SKILL_WEB_DISCOVER = Skill(
    name="Discover",
    key="d",
    description="Directory and file discovery with fuzzing",
    prompt="Discover hidden directories, files, and endpoints on the target. Use ffuf_fuzz "
           "with the default wordlist first, then try with extensions "
           ".php,.html,.js,.json,.xml,.bak,.old. Report all interesting findings.",
)

SKILL_WEB_SSL = Skill(
    name="TLS/SSL",
    key="l",
    description="Analyze TLS/SSL configuration and certificates",
    prompt="Analyze the target's TLS/SSL configuration using testssl_analyze. Report on "
           "protocol support, cipher suites, certificate details, and any vulnerabilities.",
)

SKILL_WEB_SQLI = Skill(
    name="SQLi Test",
    key="q",
    description="Test for SQL injection vulnerabilities",
    prompt="Test the target for SQL injection. Start by using http_request to identify "
           "forms and parameters, then use sqlmap_test on promising endpoints. Report findings.",
)

SKILL_WEB_MANUAL = Skill(
    name="Manual Test",
    key="m",
    description="Manual HTTP probing: headers, cookies, auth, CORS",
    prompt="Perform manual HTTP testing on the target. Use http_request to: check security "
           "headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, etc.), test CORS "
           "configuration, examine cookies (HttpOnly, Secure, SameSite flags), check for "
           "information disclosure in headers and error pages. Report all findings.",
)

SKILL_WEB_REPORT = Skill(
    name="Report",
    key="w",
    description="Generate a penetration test report of findings",
    prompt="Based on everything discovered so far, write a structured penetration test "
           "report: Executive summary, scope, methodology, findings (sorted by severity "
           "with CVSS where applicable), evidence, and remediation recommendations. "
           "Use markdown formatting.",
)


_WEB_SKILLS = [
    SKILL_WEB_RECON, SKILL_WEB_SCAN, SKILL_WEB_DISCOVER, SKILL_WEB_SSL,
    SKILL_WEB_SQLI, SKILL_WEB_MANUAL, SKILL_WEB_REPORT,
]

_WEB_API_SKILLS = [
    SKILL_WEB_RECON, SKILL_WEB_SCAN, SKILL_WEB_DISCOVER, SKILL_WEB_SSL,
    SKILL_WEB_MANUAL, SKILL_WEB_SQLI, SKILL_WEB_REPORT,
]

_WEB_RECON_SKILLS = [
    SKILL_WEB_RECON, SKILL_WEB_DISCOVER, SKILL_WEB_SSL,
    SKILL_WEB_MANUAL, SKILL_WEB_REPORT,
]


# ── Web pentest profiles ──────────────────────────────────────────

_register(Profile(
    name="Web Pentest",
    key="webpentest",
    description="Full OWASP-methodology web application penetration testing",
    system_addendum="""\

## Profile: Web Application Penetration Testing

You are performing a web application penetration test. Follow OWASP Testing Guide methodology:
1. **Reconnaissance**: Fingerprint technologies, enumerate subdomains, scan ports, detect WAFs
2. **Enumeration**: Discover directories, files, hidden endpoints, API routes
3. **Vulnerability scanning**: Run automated scanners (nuclei, nikto), check TLS
4. **Manual testing**: Focus on OWASP Top 10 — injection, broken access control, misconfig, auth failures
5. **Exploitation**: Confirm vulnerabilities with proof-of-concept when safe to do so
6. **Reporting**: Document findings with severity, evidence, and remediation

Key priorities:
- Always start with passive recon before active scanning
- Check security headers, CORS, cookies on every target
- Test authentication and session management thoroughly
- Look for IDOR, path traversal, and privilege escalation
- Check for information disclosure in error pages, headers, and comments
- Test input validation on all user-controllable parameters
""",
    skills=_WEB_SKILLS,
))

_register(Profile(
    name="Web API Pentest",
    key="webapi",
    description="REST/GraphQL API penetration testing — auth bypass, BOLA, injection",
    system_addendum="""\

## Profile: API Penetration Testing

You are testing a web API (REST, GraphQL, or similar). Focus on API-specific vulnerabilities:

### Authentication & Authorization
- Test JWT handling: algorithm confusion, weak secrets, token expiry, none algorithm
- Test OAuth flows: redirect_uri manipulation, state parameter, scope escalation
- Check API key security: key in URL vs header, key rotation, key scope
- Test for BOLA/IDOR: modify object IDs in requests to access other users' data

### API-Specific Vulnerabilities
- **BOLA (Broken Object Level Authorization)**: Change IDs in /api/users/{id}, /api/orders/{id}
- **Mass Assignment**: Send extra fields in PUT/PATCH requests (role, isAdmin, balance)
- **Excessive Data Exposure**: Check if API returns more data than the frontend uses
- **Rate Limiting**: Test for missing rate limits on sensitive endpoints (login, password reset)
- **Injection**: SQL injection in query parameters, NoSQL injection in JSON bodies

### GraphQL-Specific
- Test introspection query: `{__schema{types{name,fields{name}}}}`
- Test for query depth/complexity limits
- Check for batching attacks
- Test field-level authorization

### Methodology
1. Map all API endpoints using ffuf_fuzz and manual probing
2. Analyze authentication mechanism
3. Test authorization on every endpoint with different user contexts
4. Test input validation and injection on all parameters
5. Check rate limiting and resource consumption
""",
    skills=_WEB_API_SKILLS,
))

_register(Profile(
    name="Web Recon",
    key="webrecon",
    description="Non-intrusive web reconnaissance only — no active exploitation",
    system_addendum="""\

## Profile: Web Reconnaissance (Non-Intrusive)

You are performing reconnaissance ONLY. Do NOT attempt active exploitation, SQL injection, \
or other attacks. Your goal is to map the attack surface and identify potential areas of \
concern without causing any impact.

Allowed activities:
- Technology fingerprinting (whatweb)
- Subdomain enumeration (subfinder — passive only)
- Port scanning (nmap — service detection OK)
- TLS/SSL analysis (testssl)
- Security header review (http_request with HEAD/GET)
- Directory discovery (ffuf with small wordlists)
- Cookie and session analysis (http_request)
- WAF detection (wafw00f)
- robots.txt, sitemap.xml, .well-known analysis
- Public information gathering

NOT allowed:
- SQL injection testing
- XSS payload injection
- Authentication brute-forcing
- Active exploitation of any kind
- Heavy scanning that could cause service impact
""",
    skills=_WEB_RECON_SKILLS,
))


def get_profile(key: str) -> Profile:
    """Get a profile by key, defaulting to 'general'."""
    return PROFILES.get(key, PROFILES["general"])


def list_profiles() -> list[Profile]:
    """List all available profiles."""
    return list(PROFILES.values())
