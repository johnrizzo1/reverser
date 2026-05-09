"""CTF / Crackme challenge profile."""

from . import _register, Profile
from ._skills import (
    SKILL_TRIAGE,
    SKILL_SOLVE,
    SKILL_DECOMPILE,
    SKILL_STRINGS,
    SKILL_RUN,
    SKILL_SYSCALLS,
    SKILL_WRITEUP,
)


PROFILE_CTF = _register(Profile(
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
    skills=[
        SKILL_TRIAGE, SKILL_SOLVE, SKILL_DECOMPILE, SKILL_STRINGS,
        SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
