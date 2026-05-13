# GUI Service Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `reverser.gui_service` FastAPI process that the Electron desktop UI will talk to. Phase 0 backend only — handshake, auth, and read-only endpoints (`/api/health`, `/api/profiles`, `/api/backends`). Plus the AgentSession location refactor that unblocks the GUI without disturbing the TUI.

**Architecture:** A new `src/reverser/gui_service/` package exposes a FastAPI app behind a per-launch bearer token. The entrypoint (`python -m reverser.gui_service`) finds a free localhost port, mints a 32-byte token, writes one JSON handshake line to stdout (`{"type":"ready","port":…,"token":…,"pid":…,"version":…}`), then runs uvicorn. `AgentSession` (currently at `src/reverser/tui/session.py`) is moved to `src/reverser/agent_session.py` so the new package can import it without depending on Textual; the TUI re-imports it from the new location.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (test client), pytest, pytest-asyncio.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md`](../specs/2026-05-13-electron-desktop-ui-design.md) — sections 3, 5.

---

## File map

```
pyproject.toml                                         modify
src/reverser/agent_session.py                          create (moved from tui/session.py)
src/reverser/tui/session.py                            modify (becomes a re-export shim, then deleted)
src/reverser/tui/__init__.py                           modify (if needed)
src/reverser/tui/app.py                                modify (update import path)
src/reverser/gui_service/__init__.py                   create
src/reverser/gui_service/config.py                     create
src/reverser/gui_service/auth.py                       create
src/reverser/gui_service/app.py                        create
src/reverser/gui_service/__main__.py                   create
src/reverser/gui_service/routes/__init__.py            create
src/reverser/gui_service/routes/health.py              create
src/reverser/gui_service/routes/profiles.py            create
src/reverser/gui_service/routes/backends.py            create
tests/gui_service/__init__.py                          create
tests/gui_service/conftest.py                          create
tests/gui_service/test_auth.py                         create
tests/gui_service/test_health.py                       create
tests/gui_service/test_profiles.py                     create
tests/gui_service/test_backends.py                     create
tests/gui_service/test_handshake.py                    create
```

---

## Task 1: Add Python dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add FastAPI + uvicorn to main deps, httpx + websockets to dev deps**

```toml
[project]
name = "reverser"
version = "0.1.0"
description = "Claude-powered reverse engineering agent"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk",
    "boto3",
    "click",
    "textual>=1.0.0",
    "openai>=1.0.0",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
]

[project.scripts]
reverser = "reverser.cli:main"
reverser-harness = "reverser.harness.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "websockets>=12.0",
]
```

- [ ] **Step 2: Install in the devenv shell**

Run: `pip install -e ".[dev]"`
Expected: completes without errors. `fastapi`, `uvicorn`, `httpx`, `websockets` installed.

- [ ] **Step 3: Verify imports**

Run: `python -c "import fastapi, uvicorn, httpx, websockets; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build(gui_service): add fastapi/uvicorn deps + httpx/websockets dev deps"
```

---

## Task 2: Move `AgentSession` to `src/reverser/agent_session.py`

The file at `src/reverser/tui/session.py` defines `AgentSession`, `Exchange`, `TurnStats`. Despite living under `tui/`, none of it is Textual-specific — it just yields `AgentEvent`s from a backend. Moving it lets the GUI service import it without taking a Textual dependency.

**Files:**
- Create: `src/reverser/agent_session.py` (moved content)
- Modify: `src/reverser/tui/session.py` (replace with re-export shim, to be removed in Task 2 Step 6)
- Modify: `src/reverser/tui/app.py` (update import path)
- Test: `tests/test_agent_session_import.py`

- [ ] **Step 1: Write a smoke test that fails until the file exists at the new location**

Create `tests/test_agent_session_import.py`:

```python
"""Verify AgentSession is importable from the canonical (non-TUI) location."""


def test_agent_session_importable_from_canonical_path():
    from reverser.agent_session import AgentSession, Exchange, TurnStats
    assert AgentSession is not None
    assert Exchange is not None
    assert TurnStats is not None


def test_tui_session_still_works_for_backwards_compat():
    """Existing TUI imports must continue to function during the transition."""
    from reverser.tui.session import AgentSession as TUIAgentSession
    from reverser.agent_session import AgentSession as CanonicalAgentSession
    assert TUIAgentSession is CanonicalAgentSession
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_session_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.agent_session'`

- [ ] **Step 3: Move the file**

Run:
```bash
git mv src/reverser/tui/session.py src/reverser/agent_session.py
```

Then fix the relative imports inside `src/reverser/agent_session.py`. Change every `from ..` import to `from .` since the file is now one level closer to the package root:

```python
# Before (relative to src/reverser/tui/):
from ..prompts import SYSTEM_PROMPT, WEB_SYSTEM_PROMPT  # noqa: F401
from ..profiles import Profile
from ..tools import ALL_TOOLS
from ..tools._common import is_url
from ..backends import AgentEvent, create_backend
from ..session_log import SessionLog, session_log_path

# After (relative to src/reverser/):
from .prompts import SYSTEM_PROMPT, WEB_SYSTEM_PROMPT  # noqa: F401
from .profiles import Profile
from .tools import ALL_TOOLS
from .tools._common import is_url
from .backends import AgentEvent, create_backend
from .session_log import SessionLog, session_log_path
```

Also fix the lazy imports inside methods (`from ..sessions import ...` → `from .sessions import ...`). There are several:

- `_init_new`: `from ..sessions import (new_snapshot, save as save_snapshot, SessionConfig,)` → `from .sessions import ...`
- `_init_resumed`: `from ..sessions import save as save_snapshot, ConversationEntry` → `from .sessions import ...`
- `stop`: `from ..sessions import save as save_snapshot` → `from .sessions import ...`
- `mark_completed`: same
- `update_budget`: same
- `update_max_turns`: same
- `_autosave_snapshot`: `from ..sessions import save as save_snapshot, ConversationEntry` → `from .sessions import ...`
- `__init__` end: `from ..sessions import current_session` → `from .sessions import current_session`

- [ ] **Step 4: Create a thin re-export shim at the old location**

This preserves any out-of-tree imports during the transition. Create `src/reverser/tui/session.py`:

```python
"""Backward-compat shim. AgentSession moved to reverser.agent_session.

Import directly from the new location instead:
    from reverser.agent_session import AgentSession
"""

from ..agent_session import (  # noqa: F401
    AgentSession,
    Exchange,
    TurnStats,
)
```

- [ ] **Step 5: Update `src/reverser/tui/app.py` imports**

Find every occurrence of `from .session import ...` in `src/reverser/tui/app.py` and change to `from ..agent_session import ...`. There should be one or two import lines near the top.

Run: `grep -n "from .session" src/reverser/tui/app.py`

For each match, edit:

```python
# Before:
from .session import AgentSession, Exchange, TurnStats

# After:
from ..agent_session import AgentSession, Exchange, TurnStats
```

- [ ] **Step 6: Run import tests**

Run: `pytest tests/test_agent_session_import.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Run TUI smoke — make sure the TUI still launches**

Run:
```bash
echo "/help" | timeout 5 reverser i --help 2>&1 | head -20
```

Expected: the help text prints. No `ModuleNotFoundError`. (The smoke is `reverser i --help`, not a full TUI launch — that requires a binary and the full env. Help is the minimal "can the CLI even import its modules" check.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(agent_session): move AgentSession out of tui/ to canonical location

Adds re-export shim at src/reverser/tui/session.py to preserve any existing
imports during the GUI rollout. The shim is removed after the GUI service
ships and consumers are updated."
```

---

## Task 3: Create `gui_service` package skeleton (config + auth helpers)

**Files:**
- Create: `src/reverser/gui_service/__init__.py`
- Create: `src/reverser/gui_service/config.py`
- Create: `src/reverser/gui_service/auth.py`
- Create: `tests/gui_service/__init__.py`
- Create: `tests/gui_service/conftest.py`
- Test: `tests/gui_service/test_auth.py`

- [ ] **Step 1: Write the failing auth test**

Create `tests/gui_service/__init__.py` (empty file).

Create `tests/gui_service/conftest.py`:

```python
"""Shared fixtures for gui_service tests."""
import pytest
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def service_config() -> ServiceConfig:
    """A ServiceConfig with a known token for tests."""
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        token="test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_root=".",
    )
```

Create `tests/gui_service/test_auth.py`:

```python
"""Auth middleware unit tests.

The token check is applied at the FastAPI dependency layer; here we test
the predicate that the dependency wraps so we don't need a full app to
exercise its edge cases.
"""
from reverser.gui_service.auth import is_authorized


def test_is_authorized_accepts_correct_bearer(service_config):
    header = f"Bearer {service_config.token}"
    assert is_authorized(header, service_config.token) is True


def test_is_authorized_rejects_wrong_bearer(service_config):
    assert is_authorized("Bearer wrong-token", service_config.token) is False


def test_is_authorized_rejects_missing_header(service_config):
    assert is_authorized(None, service_config.token) is False


def test_is_authorized_rejects_empty_header(service_config):
    assert is_authorized("", service_config.token) is False


def test_is_authorized_rejects_wrong_scheme(service_config):
    assert is_authorized(f"Basic {service_config.token}", service_config.token) is False


def test_is_authorized_accepts_ws_query_token(service_config):
    """WS upgrade uses ?token=… in the query string."""
    from reverser.gui_service.auth import is_authorized_query
    assert is_authorized_query(service_config.token, service_config.token) is True
    assert is_authorized_query("wrong", service_config.token) is False
    assert is_authorized_query(None, service_config.token) is False
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.gui_service'`

- [ ] **Step 3: Create the package + config + auth modules**

Create `src/reverser/gui_service/__init__.py`:

```python
"""GUI service: FastAPI app exposing reverser to the Electron desktop UI.

Entry point: `python -m reverser.gui_service`.
"""
```

Create `src/reverser/gui_service/config.py`:

```python
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
```

Create `src/reverser/gui_service/auth.py`:

```python
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
```

- [ ] **Step 4: Run test — verify it passes**

Run: `pytest tests/gui_service/test_auth.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/ tests/gui_service/
git commit -m "feat(gui_service): package skeleton + auth predicates"
```

---

## Task 4: FastAPI app factory + 401 middleware

**Files:**
- Create: `src/reverser/gui_service/app.py`
- Test: `tests/gui_service/test_app_auth_gate.py`

- [ ] **Step 1: Write the failing test for the 401 gate**

Create `tests/gui_service/test_app_auth_gate.py`:

```python
"""The FastAPI app must reject requests without the bearer token."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def config():
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        token="test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_root=".",
    )


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_rejects_missing_bearer(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_rejects_wrong_bearer(client):
    resp = await client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_rejects_wrong_scheme(client, config):
    resp = await client.get("/api/health", headers={"Authorization": f"Basic {config.token}"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_app_auth_gate.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_app'`.

- [ ] **Step 3: Implement the app factory with auth dependency**

Create `src/reverser/gui_service/app.py`:

```python
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
```

- [ ] **Step 4: Run test — verify it passes**

Run: `pytest tests/gui_service/test_app_auth_gate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify a valid bearer returns 200**

Add to the same test file (append):

```python
@pytest.mark.asyncio
async def test_health_accepts_valid_bearer(client, config):
    resp = await client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {config.token}"},
    )
    assert resp.status_code == 200
```

Run: `pytest tests/gui_service/test_app_auth_gate.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/app.py tests/gui_service/test_app_auth_gate.py
git commit -m "feat(gui_service): FastAPI app factory + bearer-token dependency"
```

---

## Task 5: `/api/health` returns environment health snapshot

**Files:**
- Create: `src/reverser/gui_service/routes/__init__.py`
- Create: `src/reverser/gui_service/routes/health.py`
- Modify: `src/reverser/gui_service/app.py` (wire the router)
- Test: `tests/gui_service/test_health.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_health.py`:

```python
"""GET /api/health returns a snapshot of backend tool availability."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def config():
    return ServiceConfig(
        host="127.0.0.1", port=0,
        token="t", project_root=".",
    )


@pytest.fixture
async def client(config):
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_shape(client):
    resp = await client.get("/api/health", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    # Top-level keys
    assert body["ok"] is True
    assert "version" in body
    assert "checks" in body
    # Expected checks (each is {ok: bool, detail: str | null})
    checks = body["checks"]
    for key in ("python", "devenv_shell", "playwright_chromium", "msf_rpcd", "neo4j"):
        assert key in checks
        assert "ok" in checks[key]
        assert "detail" in checks[key]
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_health.py -v`
Expected: FAIL — response body missing `version` / `checks`.

- [ ] **Step 3: Implement the health router**

Create `src/reverser/gui_service/routes/__init__.py` (empty file).

Create `src/reverser/gui_service/routes/health.py`:

```python
"""GET /api/health — environment health snapshot.

Each check returns {ok: bool, detail: str | None}. ok=True means the
dependency was found; detail carries the version string or the not-found
reason. None of these checks block service startup — they are surfaced in
the UI so the operator can fix issues before launching an engagement.
"""
import os
import shutil
import sys

from fastapi import APIRouter, Depends

router = APIRouter()


def _check_python() -> dict:
    return {"ok": True, "detail": sys.version.split()[0]}


def _check_devenv_shell() -> dict:
    """We assume the user launched the service from `devenv shell`; the
    smoking-gun is the presence of `IN_NIX_SHELL` or `DEVENV_PROFILE` env
    vars. Best-effort — not a hard failure if missing."""
    in_devenv = bool(os.environ.get("IN_NIX_SHELL") or os.environ.get("DEVENV_PROFILE"))
    return {
        "ok": in_devenv,
        "detail": "IN_NIX_SHELL or DEVENV_PROFILE set" if in_devenv
        else "no devenv markers found — RE tools may be missing from PATH",
    }


def _check_binary_on_path(binary_name: str, label: str) -> dict:
    path = shutil.which(binary_name)
    return {"ok": path is not None, "detail": path or f"{label} not on PATH"}


def _check_playwright_chromium() -> dict:
    """Playwright keeps its Chromium download under ~/.cache/ms-playwright/.
    We just check that the marker directory exists; a deeper liveness check
    happens when web_browser_start runs."""
    cache = os.path.expanduser("~/.cache/ms-playwright")
    return {
        "ok": os.path.isdir(cache),
        "detail": cache if os.path.isdir(cache) else "Chromium not installed",
    }


def _build_checks() -> dict:
    return {
        "python": _check_python(),
        "devenv_shell": _check_devenv_shell(),
        "playwright_chromium": _check_playwright_chromium(),
        "msf_rpcd": _check_binary_on_path("msfrpcd", "Metasploit RPC daemon"),
        "neo4j": _check_binary_on_path("neo4j", "Neo4j"),
    }


@router.get("/api/health")
def get_health() -> dict:
    # `reverser/__init__.py` does not currently export `__version__`; fall
    # back to the pyproject version string. Version is purely informational.
    return {
        "ok": True,
        "version": "0.1.0",
        "checks": _build_checks(),
    }
```

- [ ] **Step 4: Wire the router in `app.py`**

Replace the placeholder `/api/health` in `src/reverser/gui_service/app.py`:

```python
"""FastAPI app factory for the GUI service."""
from fastapi import Depends, FastAPI, Header, HTTPException, status

from .auth import is_authorized
from .config import ServiceConfig
from .routes import health as health_routes


def _require_token_dep(config: ServiceConfig):
    def _check(authorization: str | None = Header(default=None)) -> None:
        if not is_authorized(authorization, config.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
            )
    return _check


def create_app(config: ServiceConfig) -> FastAPI:
    app = FastAPI(title="reverser GUI service", version="0.1.0")
    app.state.config = config
    require_token = Depends(_require_token_dep(config))
    app.include_router(health_routes.router, dependencies=[require_token])
    return app
```

- [ ] **Step 5: Run test — verify it passes**

Run: `pytest tests/gui_service/test_health.py -v`
Expected: PASS.

- [ ] **Step 6: Re-run the auth-gate test to confirm no regression**

Run: `pytest tests/gui_service/test_app_auth_gate.py -v`
Expected: still PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/reverser/gui_service/routes/ src/reverser/gui_service/app.py tests/gui_service/test_health.py
git commit -m "feat(gui_service): /api/health with env tool checks"
```

---

## Task 6: `/api/profiles` returns all 15 registered profiles

**Files:**
- Create: `src/reverser/gui_service/routes/profiles.py`
- Modify: `src/reverser/gui_service/app.py` (include the new router)
- Test: `tests/gui_service/test_profiles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_profiles.py`:

```python
"""GET /api/profiles enumerates the profile registry."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client():
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=".")
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_profiles_list_shape(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    assert "profiles" in body
    assert isinstance(body["profiles"], list)
    assert len(body["profiles"]) >= 15  # 15 currently registered

    # Each item has the documented shape
    one = body["profiles"][0]
    for key in ("key", "name", "description", "skills", "tools_allowlist"):
        assert key in one


@pytest.mark.asyncio
async def test_profiles_includes_known_keys(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    keys = {p["key"] for p in resp.json()["profiles"]}
    # Spot-check a few profiles we know are registered
    for k in ("general", "ctf", "manager", "webpentest", "ad"):
        assert k in keys, f"missing expected profile: {k}"


@pytest.mark.asyncio
async def test_profile_skill_shape(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    manager = next(p for p in resp.json()["profiles"] if p["key"] == "manager")
    assert isinstance(manager["skills"], list)
    if manager["skills"]:
        s = manager["skills"][0]
        for key in ("name", "key", "description"):
            assert key in s
        # We do NOT expose the skill prompt over the API (it is internal model context).
        assert "prompt" not in s
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_profiles.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Implement the profiles router**

Create `src/reverser/gui_service/routes/profiles.py`:

```python
"""GET /api/profiles — the profile registry, serialized for the UI.

We deliberately omit the skill `prompt` field. The prompt is internal
model context; the UI only needs name/key/description to render the skill
picker.
"""
from fastapi import APIRouter

from ...profiles import list_profiles

router = APIRouter()


def _serialize_skill(s) -> dict:
    return {"name": s.name, "key": s.key, "description": s.description}


def _serialize_profile(p) -> dict:
    return {
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "skills": [_serialize_skill(s) for s in p.skills],
        "tools_allowlist": p.tools_allowlist,  # None means "all tools"
    }


@router.get("/api/profiles")
def get_profiles() -> dict:
    return {"profiles": [_serialize_profile(p) for p in list_profiles()]}
```

- [ ] **Step 4: Wire the router in `app.py`**

Modify `src/reverser/gui_service/app.py` — add the import and include the router:

```python
from .routes import health as health_routes
from .routes import profiles as profiles_routes
# ...
def create_app(config: ServiceConfig) -> FastAPI:
    app = FastAPI(title="reverser GUI service", version="0.1.0")
    app.state.config = config
    require_token = Depends(_require_token_dep(config))
    app.include_router(health_routes.router, dependencies=[require_token])
    app.include_router(profiles_routes.router, dependencies=[require_token])
    return app
```

- [ ] **Step 5: Run test — verify it passes**

Run: `pytest tests/gui_service/test_profiles.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/profiles.py src/reverser/gui_service/app.py tests/gui_service/test_profiles.py
git commit -m "feat(gui_service): /api/profiles serializes the 15-profile registry"
```

---

## Task 7: `/api/backends` enumerates known backends

**Files:**
- Create: `src/reverser/gui_service/routes/backends.py`
- Modify: `src/reverser/gui_service/app.py` (include the new router)
- Test: `tests/gui_service/test_backends.py`

The Phase 0 version of `/api/backends` returns static metadata about each backend (claude, ollama, lmstudio, local) — name, default API base, whether an API key is required, whether a model must be specified. Live model-list discovery (calling `/v1/models` on the local backends) is deferred to Phase 1.

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_backends.py`:

```python
"""GET /api/backends returns metadata for each supported backend."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client():
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=".")
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_backends_shape(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    assert "backends" in body
    backends = {b["key"]: b for b in body["backends"]}
    # All four known backends present
    for key in ("claude", "ollama", "lmstudio", "local"):
        assert key in backends
        b = backends[key]
        for field in ("key", "name", "default_api_base", "requires_api_key", "requires_model"):
            assert field in b


@pytest.mark.asyncio
async def test_claude_requires_api_key(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    claude = next(b for b in resp.json()["backends"] if b["key"] == "claude")
    assert claude["requires_api_key"] is True
    assert claude["requires_model"] is False


@pytest.mark.asyncio
async def test_local_backends_require_model_not_key(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    for k in ("ollama", "lmstudio", "local"):
        b = next(x for x in resp.json()["backends"] if x["key"] == k)
        assert b["requires_api_key"] is False
        assert b["requires_model"] is True
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_backends.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Implement the backends router**

Create `src/reverser/gui_service/routes/backends.py`:

```python
"""GET /api/backends — static metadata about each supported backend.

Live model-list discovery (calling /v1/models on local backends) is a
Phase 1 feature; this endpoint returns only static metadata.
"""
from fastapi import APIRouter

router = APIRouter()


_BACKENDS = [
    {
        "key": "claude",
        "name": "Claude (Anthropic)",
        "default_api_base": None,
        "requires_api_key": True,
        "requires_model": False,
    },
    {
        "key": "ollama",
        "name": "Ollama (local)",
        "default_api_base": "http://localhost:11434/v1",
        "requires_api_key": False,
        "requires_model": True,
    },
    {
        "key": "lmstudio",
        "name": "LM Studio (local)",
        "default_api_base": "http://localhost:1234/v1",
        "requires_api_key": False,
        "requires_model": True,
    },
    {
        "key": "local",
        "name": "OpenAI-compatible (custom)",
        "default_api_base": None,
        "requires_api_key": False,
        "requires_model": True,
    },
]


@router.get("/api/backends")
def get_backends() -> dict:
    return {"backends": list(_BACKENDS)}
```

- [ ] **Step 4: Wire the router in `app.py`**

```python
from .routes import backends as backends_routes
# ... in create_app:
app.include_router(backends_routes.router, dependencies=[require_token])
```

- [ ] **Step 5: Run test — verify it passes**

Run: `pytest tests/gui_service/test_backends.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/backends.py src/reverser/gui_service/app.py tests/gui_service/test_backends.py
git commit -m "feat(gui_service): /api/backends static metadata for 4 backends"
```

---

## Task 8: `__main__.py` — handshake + uvicorn launch

**Files:**
- Create: `src/reverser/gui_service/__main__.py`
- Test: `tests/gui_service/test_handshake.py`

The entrypoint mints a per-launch token, binds a free localhost port, writes one handshake JSON line to stdout, then runs uvicorn. After the JSON line, stdout becomes free-form logs.

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_handshake.py`:

```python
"""End-to-end test: spawn the service, parse the handshake, hit endpoints.

This is a real subprocess test — it picks an unused port, spawns
`python -m reverser.gui_service --port 0 --project-root .`, reads ONE line of
stdout for the handshake, then makes a real HTTP request with the token.
The subprocess is torn down at test end.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys

import httpx
import pytest


@pytest.mark.asyncio
async def test_handshake_then_health():
    env = {**os.environ}  # inherit; service relies on PATH and devenv
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = proc.stdout.readline()
        assert line, f"no handshake line; stderr: {proc.stderr.read()}"
        handshake = json.loads(line)
        assert handshake["type"] == "ready"
        assert handshake["port"] > 0
        assert len(handshake["token"]) >= 32
        assert handshake["pid"] == proc.pid

        # The service is now listening. Hit /api/health with the token.
        await asyncio.sleep(0.1)  # tiny grace period for uvicorn to settle
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{handshake['port']}") as c:
            r = await c.get("/api/health",
                            headers={"Authorization": f"Bearer {handshake['token']}"})
            assert r.status_code == 200
            assert r.json()["ok"] is True

        # And it rejects requests without the token.
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{handshake['port']}") as c:
            r = await c.get("/api/health")
            assert r.status_code == 401
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/gui_service/test_handshake.py -v`
Expected: FAIL — `python -m reverser.gui_service` errors with "no `__main__` module".

- [ ] **Step 3: Implement `__main__.py`**

Create `src/reverser/gui_service/__main__.py`:

```python
"""Service entry point.

Mints a per-launch token, binds a free localhost port, writes one JSON
handshake line to stdout, then runs uvicorn. Stdout after the handshake
is free-form logs (forwarded from uvicorn).
"""
import argparse
import json
import os
import secrets
import socket
import sys

import uvicorn

from .app import create_app
from .config import ServiceConfig


def _find_free_port(host: str) -> int:
    """Bind a temporary socket to pick a free port, then release it.

    There is a small race window between releasing and uvicorn binding
    that we accept — collisions on a single-user machine are vanishingly
    unlikely, and uvicorn will fail loudly if it does happen.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reverser.gui_service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 = pick a free port (default)")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)

    host = args.host
    port = args.port or _find_free_port(host)
    token = secrets.token_hex(32)
    project_root = os.path.abspath(args.project_root)

    config = ServiceConfig(host=host, port=port, token=token, project_root=project_root)
    app = create_app(config)

    # Handshake on stdout — exactly one line.
    print(json.dumps({
        "type": "ready",
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "version": "0.1.0",
    }), flush=True)

    # Hand off to uvicorn. Logs from here on are free-form on stdout/stderr.
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        # Crucial: stop uvicorn from installing its own signal handlers,
        # so SIGTERM from the supervisor exits cleanly.
        # (uvicorn's default behavior is already SIGTERM-clean; this is just
        # documentation of intent.)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test — verify it passes**

Run: `pytest tests/gui_service/test_handshake.py -v`
Expected: PASS.

If the test hangs, check that uvicorn isn't buffering stdout. The `-u` flag on the subprocess command and `flush=True` on the handshake print should prevent that.

- [ ] **Step 5: Manual smoke**

Run, from a separate terminal in the devenv shell:

```bash
python -m reverser.gui_service --port 0 --project-root .
```

Expected: prints one JSON line like `{"type":"ready","port":52341,"token":"…","pid":12345,"version":"0.1.0"}`, then uvicorn startup logs follow. From another terminal:

```bash
PORT=52341 TOKEN=<paste>
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/profiles | head -c 200
```

Expected: JSON with a `profiles` array. Ctrl-C the service.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/__main__.py tests/gui_service/test_handshake.py
git commit -m "feat(gui_service): __main__ handshake + uvicorn launch"
```

---

## Task 9: Cross-cutting smoke test — `/api/profiles` via the spawned service

This test catches integration issues between the routers, the handshake, and the auth dep, by hitting `/api/profiles` against the real subprocess from Task 8. (The handshake test only hits `/api/health` to keep the failure surface small.)

**Files:**
- Modify: `tests/gui_service/test_handshake.py` (append new test)

- [ ] **Step 1: Append the failing test**

Add to the end of `tests/gui_service/test_handshake.py`:

```python
@pytest.mark.asyncio
async def test_handshake_full_endpoint_surface():
    """Spawn the service and verify /api/profiles + /api/backends round-trip."""
    env = {**os.environ}
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = proc.stdout.readline()
        handshake = json.loads(line)
        base = f"http://127.0.0.1:{handshake['port']}"
        headers = {"Authorization": f"Bearer {handshake['token']}"}
        await asyncio.sleep(0.1)
        async with httpx.AsyncClient(base_url=base) as c:
            profiles = await c.get("/api/profiles", headers=headers)
            assert profiles.status_code == 200
            assert len(profiles.json()["profiles"]) >= 15
            backends = await c.get("/api/backends", headers=headers)
            assert backends.status_code == 200
            assert {b["key"] for b in backends.json()["backends"]} >= {"claude", "ollama", "lmstudio", "local"}
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
```

- [ ] **Step 2: Run — verify it passes**

Run: `pytest tests/gui_service/test_handshake.py -v`
Expected: PASS (both handshake tests).

- [ ] **Step 3: Run the entire gui_service test suite**

Run: `pytest tests/gui_service/ -v`
Expected: PASS — auth (6), app gate (4), health, profiles (3), backends (3), handshake (2). Total ~20+.

- [ ] **Step 4: Commit**

```bash
git add tests/gui_service/test_handshake.py
git commit -m "test(gui_service): smoke /api/profiles + /api/backends via spawned subprocess"
```

---

## Verification

After all tasks, run the full project test suite to confirm no regressions:

```bash
pytest -v
```

Expected: existing tests still PASS, plus the new `tests/gui_service/` tests. The TUI should still launch (`reverser i --help` returns help text).

Manual smoke (from `devenv shell`):

```bash
python -m reverser.gui_service --port 0 --project-root .
# Note the {port, token} from stdout
# In another terminal:
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/health
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/profiles | python -m json.tool | head -40
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/backends | python -m json.tool
# All three return non-empty JSON. /api/health without the header returns 401.
```

## What this plan does NOT cover

These items live in Plan 2 (Electron shell) and Plan 3 (Live Command Center MVP):

- The Electron app itself (Plan 2).
- The `desktop/` directory and supervisor (Plan 2).
- Any `POST` endpoint that creates or mutates a session (Plan 3).
- The WebSocket endpoint (Plan 3).
- The `GUISession` adapter wrapping `AgentSession` for the GUI (Plan 3).
- KB read endpoints, findings, scope, sudo, skills, messages (Plan 3).
- Removal of the `src/reverser/tui/session.py` re-export shim (Plan 3, after consumers update).
