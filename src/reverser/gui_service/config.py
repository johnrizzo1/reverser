"""Service configuration (in-process; no on-disk config file)."""
from dataclasses import dataclass


@dataclass
class ServiceConfig:
    """Per-launch service configuration.

    The token is minted by __main__ at process start (32 random bytes hex)
    and survives only for the lifetime of the process. It is never written
    to disk.
    """
    host: str
    port: int
    token: str
    project_root: str
