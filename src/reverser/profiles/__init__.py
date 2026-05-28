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
    tools_allowlist: list[str] | None = None  # None = all tools available
    domain: str = "binary"


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


def profile_domain(key: str, *, registry: dict[str, Profile] | None = None) -> str:
    """Return a profile's domain classification.

    Domains are intentionally coarse: "binary", "web", or "network".
    Unknown domains fall back to "binary" to preserve legacy behavior for
    profiles that have not been annotated yet.
    """
    profiles = PROFILES if registry is None else registry
    return profiles[key].domain or "binary"


def is_web_profile(key: str, *, registry: dict[str, Profile] | None = None) -> bool:
    """True when a profile is specifically for web applications."""
    return profile_domain(key, registry=registry) == "web"


def is_network_profile(key: str, *, registry: dict[str, Profile] | None = None) -> bool:
    """True when a profile touches network targets, including web profiles."""
    return profile_domain(key, registry=registry) in {"web", "network"}


# ── Profile module imports (each registers itself on import) ────────
from . import (  # noqa: F401, E402  # imported for side effects
    general,
    linux,
    windows,
    android,
    chrome,
    managed,
    api,
    pentest,
    webpentest,
    webapi,
    webrecon,
    ad,
    ctf,
    manager,
    exploit,
)
