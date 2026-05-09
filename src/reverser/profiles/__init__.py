"""Agent profiles for specialized reverse engineering / pentest workflows.

Each profile lives in its own module under this package. The package's
__init__.py owns the dataclasses, registry, and lookup helpers.
"""

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A pre-packaged action the user can trigger."""
    name: str
    key: str
    description: str
    prompt: str


@dataclass
class Profile:
    """An agent profile that specializes behavior for a target type."""
    name: str
    key: str
    description: str
    system_addendum: str
    skills: list[Skill] = field(default_factory=list)


PROFILES: dict[str, Profile] = {}


def _register(p: Profile) -> Profile:
    """Register a profile in the global registry. Returns the profile."""
    PROFILES[p.key] = p
    return p


def get_profile(key: str) -> Profile:
    """Look up a profile by key. Raises KeyError if unknown."""
    if key not in PROFILES:
        raise KeyError(
            f"Unknown profile: {key!r}. Known: {sorted(PROFILES.keys())}"
        )
    return PROFILES[key]


def list_profiles() -> list[Profile]:
    """Return all registered profiles, sorted by key."""
    return [PROFILES[k] for k in sorted(PROFILES.keys())]


# ── Profile module imports (each registers itself on import) ────────
# These will be added one-by-one as profile modules are created.
