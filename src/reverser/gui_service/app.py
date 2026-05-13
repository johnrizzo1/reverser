"""FastAPI app factory for the GUI service."""
from fastapi import Depends, FastAPI, Header, HTTPException, status

from .auth import is_authorized
from .config import ServiceConfig


def _require_token_dep(config: ServiceConfig):
    """Build a FastAPI dependency that validates the Bearer token."""
    def _check(authorization: str | None = Header(default=None)) -> None:
        if not is_authorized(authorization, config.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
            )
    return _check


def create_app(config: ServiceConfig) -> FastAPI:
    """Build a FastAPI app for the given service config.

    Every API route under /api lives behind the bearer-token dependency.
    """
    app = FastAPI(title="reverser GUI service", version="0.1.0")
    app.state.config = config

    require_token = Depends(_require_token_dep(config))

    # Route modules will be wired in subsequent tasks.
    # Placeholder /api/health is added here so the auth gate is testable now.
    @app.get("/api/health", dependencies=[require_token])
    def _health_placeholder():
        return {"ok": True}

    return app
