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

SKILL_SYSCALLS = Skill(
    name="Syscalls",
    key="y",
    description="Trace system calls during execution",
    prompt="Run the binary under strace to observe its syscall behavior. Focus on "
           "file operations, network calls, and process manipulation. Summarize "
           "the syscall patterns.",
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
    SKILL_TRIAGE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
    SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
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
    SKILL_WEB_RECON, SKILL_WEB_SCAN, SKILL_WEB_DISCOVER,
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
