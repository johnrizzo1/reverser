"""GET /api/health — environment health snapshot.

Each check returns {ok: bool, detail: str | None}. ok=True means the
dependency was found; detail carries the version string or the not-found
reason. None of these checks block service startup — they are surfaced in
the UI so the operator can fix issues before launching an engagement.
"""
import os
import shutil
import sys

from fastapi import APIRouter

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
