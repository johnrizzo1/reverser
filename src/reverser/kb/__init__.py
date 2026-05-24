"""Per-target persistent knowledge base for reverser engagements.

Public API:
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(...))
    kb.get_credentials(status="valid")
"""

from .store import (
    KB,
    HostFact,
    ServiceFact,
    CredentialFact,
    FindingFact,
    ArtifactFact,
    CredResult,
    normalize_target,
)
from .authz import require_pentest_auth, AuthorizationError
from .scope import Scope, ScopeError, load_scope

__all__ = [
    "for_target",
    "list_targets",
    "KB",
    "HostFact",
    "ServiceFact",
    "CredentialFact",
    "FindingFact",
    "ArtifactFact",
    "CredResult",
    "require_pentest_auth",
    "AuthorizationError",
    "normalize_target",
    "Scope",
    "ScopeError",
    "load_scope",
]


_kb_cache: dict[str, KB] = {}


def for_target(target: str) -> KB:
    """Return a cached KB instance for the given target.

    The instance is created on first call (with directory + DB initialization)
    and reused on subsequent calls within the same process.
    """
    target_id = normalize_target(target)
    if target_id not in _kb_cache:
        _kb_cache[target_id] = KB(target_id)
    return _kb_cache[target_id]


from reverser.paths import targets_root


def list_targets() -> list[str]:
    """Return all target IDs that have a state.db on disk."""
    root = targets_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "state.db").is_file()
    )
