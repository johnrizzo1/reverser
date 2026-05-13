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
    """Probe Playwright's browser cache. Default location is per-OS:
       - macOS:   ~/Library/Caches/ms-playwright
       - Linux:   ~/.cache/ms-playwright
       - Windows: %USERPROFILE%\\AppData\\Local\\ms-playwright
    Honors PLAYWRIGHT_BROWSERS_PATH if set. We only check that *some*
    chromium-* subdirectory exists; a deeper liveness check happens when
    web_browser_start runs."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates: list[str] = []
    if override:
        candidates.append(override)
    candidates.extend([
        os.path.expanduser("~/Library/Caches/ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/AppData/Local/ms-playwright"),
    ])
    for path in candidates:
        if not os.path.isdir(path):
            continue
        # Chromium dirs are named like chromium-1223 / chromium_headless_shell-1223.
        try:
            has_chromium = any(
                entry.startswith("chromium") for entry in os.listdir(path)
            )
        except OSError:
            continue
        if has_chromium:
            return {"ok": True, "detail": path}
    return {"ok": False, "detail": "Chromium not installed (run `npx playwright install chromium`)"}


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
