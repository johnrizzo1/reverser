"""Linux ELF binary analysis profile."""

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


PROFILE_LINUX = _register(Profile(
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
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
