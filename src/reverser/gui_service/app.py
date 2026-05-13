"""FastAPI app factory for the GUI service."""
from fastapi import Depends, FastAPI, Header, HTTPException, status

from .auth import is_authorized
from .config import ServiceConfig
from .event_bus import EventBus
from .session_manager import SessionManager
from .routes import (
    backends as backends_routes,
    health as health_routes,
    profiles as profiles_routes,
    sessions as sessions_routes,
)


def _require_token_dep(config: ServiceConfig):
    def _check(authorization: str | None = Header(default=None)) -> None:
        if not is_authorized(authorization, config.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return _check


def create_app(config: ServiceConfig) -> FastAPI:
    app = FastAPI(title="reverser GUI service", version="0.1.0")
    app.state.config = config
    app.state.event_bus = EventBus()
    app.state.session_manager = SessionManager(bus=app.state.event_bus)

    require_token = Depends(_require_token_dep(config))
    app.include_router(health_routes.router, dependencies=[require_token])
    app.include_router(profiles_routes.router, dependencies=[require_token])
    app.include_router(backends_routes.router, dependencies=[require_token])
    app.include_router(sessions_routes.router, dependencies=[require_token])
    return app
