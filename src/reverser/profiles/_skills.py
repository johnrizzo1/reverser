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
