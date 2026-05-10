"""Windows PE binary analysis profile."""

from . import _register, Profile
from ._skills import (
    SKILL_TRIAGE,
    SKILL_ANALYZE,
    SKILL_STRINGS,
    SKILL_DECOMPILE,
    SKILL_IMPORTS,
    SKILL_RUN,
    SKILL_SYSCALLS,
    SKILL_WRITEUP,
)


PROFILE_WINDOWS = _register(Profile(
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
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
