"""General reverse engineering profile."""

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


PROFILE_GENERAL = _register(Profile(
    name="General",
    key="general",
    description="Broad reverse engineering — works for any binary type",
    system_addendum="",
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
