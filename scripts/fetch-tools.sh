#!/usr/bin/env bash
# Download + verify + extract bundled tool binaries into
# desktop/resources/tools/<platform>/. Run from the repo root.
#
# Modes:
#   ./scripts/fetch-tools.sh <platform>                 # normal: download + verify
#   ./scripts/fetch-tools.sh --capture-shas <platform>  # download + print SHAs (no verify)

set -euo pipefail

CAPTURE_SHAS=0
if [ "${1:-}" = "--capture-shas" ]; then
  CAPTURE_SHAS=1
  shift
fi

PLATFORM="${1:?usage: fetch-tools.sh [--capture-shas] <platform>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TOML="$ROOT/scripts/tool-versions.toml"
OUT="$ROOT/desktop/resources/tools/$PLATFORM"
TMP="$(mktemp -d)"
trap "rm -rf '$TMP'" EXIT

mkdir -p "$OUT"

# Resolve a python3 interpreter that has tomllib (Python >= 3.11).
# macOS ships python3 at 3.9 which lacks it; Homebrew/devenv supply 3.11+.
find_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1; then
      if "$py" -c "import tomllib" 2>/dev/null; then
        echo "$py"
        return 0
      fi
    fi
  done
  echo "[fetch-tools] ERROR: no python3 >= 3.11 with tomllib found" >&2
  exit 1
}
PYTHON="$(find_python)"

# Inline TOML parsing via Python — bash can't read TOML natively.
read_field() {
  local section="$1" field="$2"
  "$PYTHON" -c "
import tomllib, sys
with open('$TOML', 'rb') as f:
    d = tomllib.load(f)
s = d.get('$section', {})
plat = s.get('$PLATFORM', {})
v = plat.get('$field', '') if isinstance(plat, dict) else ''
print(v)
"
}

verify_sha() {
  local file="$1" expected="$2"
  local actual
  if command -v sha256sum >/dev/null; then
    actual="$(sha256sum "$file" | cut -d' ' -f1)"
  else
    actual="$(shasum -a 256 "$file" | cut -d' ' -f1)"
  fi
  if [ "$CAPTURE_SHAS" = "1" ]; then
    echo "  $actual  ($file)"
    return 0
  fi
  if [ -z "$expected" ]; then
    echo "[fetch-tools] FAIL: no SHA pinned for $file"
    exit 1
  fi
  if [ "$actual" != "$expected" ]; then
    echo "[fetch-tools] FAIL: SHA mismatch for $file"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    exit 1
  fi
}

fetch_and_extract_archive() {
  local section="$1" extract_cmd="$2"
  local url; url="$(read_field "$section" "url")"
  local sha; sha="$(read_field "$section" "sha256")"
  if [ -z "$url" ]; then
    echo "[fetch-tools] no $section for $PLATFORM; skipping"
    return 0
  fi
  echo "[fetch-tools] $section: $url"
  local archive="$TMP/$section-archive"
  curl -fsSL "$url" -o "$archive"
  verify_sha "$archive" "$sha"
  echo "[fetch-tools] extracting $section..."
  eval "$extract_cmd"
}

# --- nmap ---
case "$PLATFORM" in
  Darwin-arm64)
    fetch_and_extract_archive nmap '
      MOUNT_POINT="$TMP/nmap-mount"
      mkdir -p "$MOUNT_POINT"
      hdiutil attach -nobrowse -mountpoint "$MOUNT_POINT" "$archive" >/dev/null
      # The .dmg contains a flat xar .mpkg archive (not a directory).
      # The xar contains sub-packages directly at the top level:
      #   nmap.pkg/Payload  (gzip+cpio, binary lands at bin/nmap)
      # Layout confirmed for nmap 7.95; update if upstream changes packaging.
      MPKG="$(find "$MOUNT_POINT" -maxdepth 1 -name "*.mpkg" | head -1)"
      if [ -z "$MPKG" ]; then
        echo "[fetch-tools] ERROR: no .mpkg found in nmap DMG" >&2
        hdiutil detach "$MOUNT_POINT" >/dev/null
        exit 1
      fi
      # Extract nmap.pkg/Payload from the flat xar archive
      NMAP_XAR="$TMP/nmap-xar"
      mkdir -p "$NMAP_XAR"
      (cd "$NMAP_XAR" && xar -xf "$MPKG" nmap.pkg/Payload)
      PAYLOAD="$NMAP_XAR/nmap.pkg/Payload"
      if [ ! -f "$PAYLOAD" ]; then
        echo "[fetch-tools] ERROR: nmap.pkg/Payload not found in mpkg" >&2
        hdiutil detach "$MOUNT_POINT" >/dev/null
        exit 1
      fi
      mkdir -p "$TMP/nmap-payload"
      (cd "$TMP/nmap-payload" && gunzip -c "$PAYLOAD" | cpio -idm 2>/dev/null)
      NMAP_BIN="$(find "$TMP/nmap-payload" -name "nmap" -type f | head -1)"
      if [ -z "$NMAP_BIN" ]; then
        echo "[fetch-tools] ERROR: nmap binary not found in payload" >&2
        hdiutil detach "$MOUNT_POINT" >/dev/null
        exit 1
      fi
      cp "$NMAP_BIN" "$OUT/nmap"
      hdiutil detach "$MOUNT_POINT" >/dev/null
    '
    ;;
  Linux-x86_64)
    fetch_and_extract_archive nmap '
      mkdir -p "$TMP/nmap-rpm"
      (cd "$TMP/nmap-rpm" && rpm2cpio "$archive" | cpio -idm 2>/dev/null)
      cp "$TMP/nmap-rpm/usr/bin/nmap" "$OUT/nmap"
    '
    ;;
esac

# --- ffuf ---
fetch_and_extract_archive ffuf '
  tar -xzf "$archive" -C "$TMP" ffuf
  cp "$TMP/ffuf" "$OUT/ffuf"
'

# --- gobuster ---
fetch_and_extract_archive gobuster '
  tar -xzf "$archive" -C "$TMP" gobuster
  cp "$TMP/gobuster" "$OUT/gobuster"
'

# --- nuclei ---
fetch_and_extract_archive nuclei '
  unzip -o "$archive" nuclei -d "$TMP" >/dev/null
  cp "$TMP/nuclei" "$OUT/nuclei"
'

# --- Playwright Chromium ---
echo "[fetch-tools] playwright_chromium: invoking npx playwright install"
PLAYWRIGHT_BROWSERS_PATH="$OUT/playwright" \
  npx --prefix "$ROOT/desktop" playwright install chromium

chmod +x "$OUT"/nmap "$OUT"/ffuf "$OUT"/gobuster "$OUT"/nuclei 2>/dev/null || true

# Generate LICENSES.md
"$PYTHON" - <<EOF > "$OUT/LICENSES.md"
import tomllib
with open("$TOML", "rb") as f:
    d = tomllib.load(f)
print("# Bundled tool licenses")
print()
print("This directory contains tools distributed under permissive licenses.")
print("Source URLs and license names are listed below; full license texts are")
print("available upstream.")
print()
for name, sect in d.items():
    ver = sect.get("version", "(see playwright npm)")
    lic = sect.get("license", "?")
    plat = sect.get("$PLATFORM")
    url = plat.get("url") if isinstance(plat, dict) else "(managed by playwright)"
    print(f"## {name}")
    print(f"- Version: {ver}")
    print(f"- License: {lic}")
    print(f"- Source: {url}")
    print()
EOF

echo "[fetch-tools] OK: $OUT"
ls -la "$OUT"
