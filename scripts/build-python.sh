#!/usr/bin/env bash
# Build the reverser Python service as a standalone --onedir bundle via PyInstaller.
# Output: desktop/python-dist/reverser-service/
#
# Idempotent: deletes any prior python-dist before rebuilding. The CI matrix
# runs this on one platform per job — there's no platform subdir.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DESKTOP="$ROOT/desktop"
OUT="$DESKTOP/python-dist/reverser-service"

# Locate pyinstaller: honour an explicit override, then search in order:
#   1. PATH (works when running inside `devenv shell` or a venv)
#   2. The devenv venv (present when building from a plain shell in this repo)
#   3. The Python user-scripts dir (macOS/Linux `pip install --user pyinstaller`)
if [[ -n "${PYINSTALLER:-}" ]]; then
  : # caller-supplied override — use as-is
elif command -v pyinstaller &>/dev/null; then
  PYINSTALLER="pyinstaller"
elif [[ -x "$ROOT/.devenv/state/venv/bin/pyinstaller" ]]; then
  PYINSTALLER="$ROOT/.devenv/state/venv/bin/pyinstaller"
else
  _user_scripts="$(python3 -c "import sysconfig; print(sysconfig.get_path('scripts','posix_user'))" 2>/dev/null || true)"
  if [[ -x "${_user_scripts}/pyinstaller" ]]; then
    PYINSTALLER="${_user_scripts}/pyinstaller"
  else
    echo "[build-python] ERROR: pyinstaller not found. Run: pip install pyinstaller" >&2
    exit 1
  fi
fi

echo "[build-python] building PyInstaller bundle for $(uname -s)-$(uname -m)"
rm -rf "$DESKTOP/python-dist" "$DESKTOP/build"

# PyInstaller writes spec-relative output. Run it from the desktop/ dir so
# distpath="python-dist" resolves to desktop/python-dist/.
cd "$DESKTOP"
"$PYINSTALLER" --noconfirm --clean --distpath python-dist reverser-service.spec

# Sanity-check: the entry binary exists and is executable
test -x "$OUT/reverser-service" \
  || { echo "[build-python] FAIL: $OUT/reverser-service not produced"; exit 1; }

echo "[build-python] OK: $OUT/reverser-service"
