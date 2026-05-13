"""Auth predicates for REST (Bearer header) and WS (?token=…) entry points.

The full FastAPI dependency wrappers live in app.py; this module owns the
constant-time string comparison so it is independently unit-testable.
"""
import hmac


def is_authorized(authorization_header: str | None, expected_token: str) -> bool:
    """Return True iff the Authorization header matches `Bearer <expected_token>`.

    Uses hmac.compare_digest for constant-time comparison to avoid leaking
    token contents through response-timing side channels.
    """
    if not authorization_header:
        return False
    parts = authorization_header.split(" ", 1)
    if len(parts) != 2:
        return False
    scheme, token = parts
    if scheme != "Bearer":
        return False
    return hmac.compare_digest(token, expected_token)


def is_authorized_query(query_token: str | None, expected_token: str) -> bool:
    """Return True iff the WS query ?token=… matches `expected_token`."""
    if not query_token:
        return False
    return hmac.compare_digest(query_token, expected_token)
