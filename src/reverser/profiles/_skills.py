"""Shared skill constants used by multiple profiles.

Single-profile skills should live in that profile's own module.
"""

from . import Skill  # forward import; resolved after __init__.py defines Skill


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

SKILL_WEB_RECON = Skill(
    name="Web Recon",
    key="r",
    description="Reconnaissance: subdomain enum, port scan, fingerprinting, WAF detection",
    prompt="Perform web reconnaissance on the target. Run in parallel: nmap_scan for open "
           "ports, subfinder_enum for subdomains, whatweb_fingerprint for technology stack, "
           "and wafw00f_detect for WAF detection. Summarize the attack surface.\n\n"
           "After recon completes, propose 3 root hypotheses about the most exploitable "
           "surface. Call kb_add_hypothesis for each one — a hypothesis is a falsifiable "
           "CLAIM (\"The /api/users endpoint is vulnerable to IDOR via the id parameter\"), "
           "not a TODO item (\"Look at the API\"). Confidence values: 80+ = strong "
           "evidence, 50 = plausible, <30 = long shot. Subsequent skills work through "
           "these in confidence order.",
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
           "forms and parameters, then use sqlmap_test on promising endpoints. Report findings.\n\n"
           "Before running, call kb_list_hypotheses status=proposed and pick the "
           "highest-confidence unconfirmed one mentioning SQLi / injection. State the "
           "hypothesis OUT LOUD. After sqlmap_test returns, call "
           "kb_update_hypothesis(id=X, status=...) with confirmed/refuted/inconclusive "
           "plus a one-line outcome. Five-failure pivot: if you've made 5 failed SQLi "
           "attempts against this hypothesis, mark it refuted, STOP, and propose three "
           "orthogonal hypotheses (different endpoint, different injection class like "
           "SSRF or auth bypass, or different auth tier).",
)

SKILL_WEB_MANUAL = Skill(
    name="Manual Test",
    key="m",
    description="Manual HTTP probing: headers, cookies, auth, CORS",
    prompt="Perform manual HTTP testing on the target. Use http_request to: check security "
           "headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, etc.), test CORS "
           "configuration, examine cookies (HttpOnly, Secure, SameSite flags), check for "
           "information disclosure in headers and error pages. Report all findings.\n\n"
           "Manual probing is the second-most-common place to grind past usefulness "
           "(after fuzzing). Anchor every probe to a hypothesis: before checking a "
           "header / cookie / CORS / form, state which hypothesis you're testing. After "
           "5 failed manual probes against the same hypothesis (5 different "
           "headers/cookies/parameters that all came back clean), mark the hypothesis "
           "refuted via kb_update_hypothesis and pivot.",
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
