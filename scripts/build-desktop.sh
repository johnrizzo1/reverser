#!/usr/bin/env bash
# Build the desktop app. Assumes PyInstaller bundle and tools fetch already done.
# Modes:
#   ./scripts/build-desktop.sh           # --dir (unpacked, fast iteration)
#   ./scripts/build-desktop.sh --installer   # full installer (.dmg/.AppImage)

set -euo pipefail

MODE="--dir"
if [ "${1:-}" = "--installer" ]; then
  MODE=""   # default electron-builder mode = full installer
  shift
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

# Sync version from pyproject.toml -> desktop/package.json
node "$ROOT/scripts/sync-version.mjs"

cd "$ROOT/desktop"

echo "[build-desktop] installing npm deps"
npm ci

echo "[build-desktop] building renderer + main process"
npm run build

echo "[build-desktop] running electron-builder (mode='$MODE')"
if [ "$MODE" = "--dir" ]; then
  npx electron-builder --dir
else
  npx electron-builder
fi

echo "[build-desktop] OK: artifacts in desktop/dist/"
ls -la "$ROOT/desktop/dist"
