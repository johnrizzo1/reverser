"""Pentest authorization gate shared by all active-testing tools."""

import os


class AuthorizationError(RuntimeError):
    """Raised when pentest authorization is required but not present."""


def require_pentest_auth() -> None:
    """Raise AuthorizationError unless pentest authorization is granted.

    Authorization sources (either is sufficient):
    - REVERSER_PENTEST_AUTHORIZED=1 environment variable
    - .reverser-authorized file in the current working directory
    """
    if os.environ.get("REVERSER_PENTEST_AUTHORIZED") == "1":
        return
    if os.path.exists(".reverser-authorized"):
        return
    raise AuthorizationError(
        "Pentest authorization required. "
        "Set REVERSER_PENTEST_AUTHORIZED=1 or create a .reverser-authorized "
        "file in the working directory to confirm you have written authorization "
        "to test the target."
    )
