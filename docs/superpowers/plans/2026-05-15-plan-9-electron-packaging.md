# Electron Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce production-ready installers (`.dmg` for macOS arm64, `.AppImage` for Linux x64) containing the Electron app, a PyInstaller-bundled Python service, and a curated set of pentest binaries (`nmap`, Playwright Chromium, `ffuf`, `gobuster`, `nuclei`).

**Architecture:** PyInstaller `--onedir` produces a standalone Python service that Electron spawns directly in packaged mode (with a dev-mode fallback for source-tree runs). electron-builder bundles the Python output plus pre-downloaded tool binaries via `extraResources`. The Electron supervisor appends the bundled-tools directory to `PATH` so existing `subprocess.run(["nmap", ...])` callers find the bundled binary when no system one exists (system-first resolution preserves curated NixOS/Homebrew installs). Shared shell scripts run both locally (via devenv) and in CI.

**Tech Stack:** PyInstaller 6.x, electron-builder 25.x, electron-updater (metadata only in v1), GitHub Actions, Node 20, Python 3.12.

**Reference spec:** [docs/superpowers/specs/2026-05-15-electron-packaging-design.md](../specs/2026-05-15-electron-packaging-design.md)

---

## File Map

**Backend / scripts (create):**
- `desktop/reverser-service.spec` — PyInstaller spec for the Python service
- `scripts/tool-versions.toml` — pinned versions, URLs, SHAs for each bundled tool
- `scripts/fetch-tools.sh` — downloads + verifies + extracts tool binaries
- `scripts/build-python.sh` — wraps `pyinstaller` invocation
- `scripts/build-desktop.sh` — wraps `npm ci && npm run build && electron-builder`
- `scripts/sync-version.mjs` — reads `pyproject.toml` version, writes to `desktop/package.json`

**Frontend / electron (create):**
- `desktop/electron-builder.yml` — electron-builder config
- `desktop/tests/packaged/smoke.spec.ts` — Playwright spec that launches the built installer

**CI (create):**
- `.github/workflows/build.yml` — matrix build on PR and push to main
- `.github/workflows/release.yml` — tag-triggered publish + gitea mirror

**Docs (create):**
- `docs/release-checklist.md` — manual verification checklist before tagging a release

**Modify:**
- `desktop/electron/python.ts` — add `isPackaged`, `resolveSpawnCommand`, extend `buildPythonEnv` for packaged mode, extend `defaultProjectRoot`
- `desktop/electron/main.ts` — add `ensureProjectRootExists` call before spawn
- `desktop/package.json` — add `electron-builder` + `electron-updater` devDeps, add scripts
- `devenv.nix` — add `scripts.package` + `scripts.package-installer`
- `.gitignore` — ignore build outputs
- `README.md` — install instructions, macOS xattr workaround

---

## Conventions used in this plan

- **Working directory:** repo root (`/Users/jrizzo/Projects/gitea/johnrizzo1/reverser`) unless otherwise stated.
- **Platform identifier:** scripts use `<platform>` arg with values `Darwin-arm64` or `Linux-x86_64` (from `$(uname -s)-$(uname -m)`). Inside electron-builder config the platform names are `mac` / `linux`.
- **Co-author trailer:** every commit uses `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **TDD where applicable:** tasks 3 (supervisor changes) follow strict TDD. Tasks 1, 2, 4, 5, 7, 8 are infrastructure tasks where "verify by running the command" replaces "verify by running the test."

---

## Task 1: PyInstaller spec + build-python.sh

**Files:**
- Create: `desktop/reverser-service.spec`
- Create: `scripts/build-python.sh`

- [ ] **Step 1: Confirm PyInstaller is available**

Run: `pip install pyinstaller && pyinstaller --version`
Expected: prints a version string like `6.11.0`. (PyInstaller is a build-only dep; do NOT add it to `pyproject.toml`'s runtime deps.)

- [ ] **Step 2: Write the PyInstaller spec**

Create `desktop/reverser-service.spec`:

```python
# PyInstaller spec for the reverser GUI service.
# Build with: pyinstaller desktop/reverser-service.spec
# Output: desktop/python-dist/reverser-service/
#
# We use --onedir mode (the default for a .spec file with COLLECT) for
# instant startup and simpler code signing. The Electron supervisor spawns
# desktop/python-dist/reverser-service/reverser-service directly.
#
# CI runs this on one platform per matrix job; there's no platform-disambiguation
# subdir under python-dist/. Local developers who switch platforms should
# `rm -rf desktop/python-dist/` before rebuilding.

from pathlib import Path

block_cipher = None

# Entry point: re-use the existing __main__.py inside the gui_service package.
# This is the same code that runs today when you invoke `python -m reverser.gui_service`.
a = Analysis(
    ["../src/reverser/gui_service/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # FastAPI/Starlette/Pydantic v2 use runtime introspection that
        # PyInstaller's static analysis sometimes misses. Listing the
        # modules explicitly is the standard mitigation.
        "uvicorn.logging",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.loops",
        "uvicorn.loops.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only deps — never used at runtime
        "pytest",
        "pytest_asyncio",
        "_pytest",
        # GUI toolkit not used
        "tkinter",
        # Jupyter / IPython not used
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="reverser-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="reverser-service",
    distpath="python-dist",
)
```

- [ ] **Step 3: Write build-python.sh**

Create `scripts/build-python.sh`:

```bash
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

echo "[build-python] building PyInstaller bundle for $(uname -s)-$(uname -m)"
rm -rf "$DESKTOP/python-dist" "$DESKTOP/build"

# PyInstaller writes spec-relative output. Run it from the desktop/ dir so
# distpath="python-dist" resolves to desktop/python-dist/.
cd "$DESKTOP"
pyinstaller --noconfirm --clean reverser-service.spec

# Sanity-check: the entry binary exists and is executable
test -x "$OUT/reverser-service" \
  || { echo "[build-python] FAIL: $OUT/reverser-service not produced"; exit 1; }

echo "[build-python] OK: $OUT/reverser-service"
```

Then:
```bash
chmod +x scripts/build-python.sh
```

- [ ] **Step 4: Run the build and verify the handshake works**

Run: `./scripts/build-python.sh`
Expected: ends with `[build-python] OK: <abs path>/desktop/python-dist/reverser-service/reverser-service`.

Then verify the bundle actually handshakes:
```bash
./desktop/python-dist/reverser-service/reverser-service \
  --host 127.0.0.1 --port 0 \
  --project-root /tmp/reverser-test 2>&1 | head -3
```
Expected: prints a JSON line on the first line that includes `"type":"ready"` and `"port":<some-int>` and `"token":"<hex>"`. Hit Ctrl-C to stop.

If you see `ModuleNotFoundError` instead, add the missing module to `hiddenimports` in `desktop/reverser-service.spec` and re-run.

- [ ] **Step 5: Update .gitignore**

Edit `.gitignore` — append:

```
# Electron packaging build outputs
desktop/python-dist/
desktop/build/
desktop/dist/
desktop/resources/tools/
```

- [ ] **Step 6: Commit**

```bash
git add desktop/reverser-service.spec scripts/build-python.sh .gitignore
git commit -m "feat(packaging): PyInstaller spec + build-python.sh for the Python service"
```

---

## Task 2: Tool fetcher (tool-versions.toml + fetch-tools.sh)

**Files:**
- Create: `scripts/tool-versions.toml`
- Create: `scripts/fetch-tools.sh`

- [ ] **Step 1: Write tool-versions.toml**

Create `scripts/tool-versions.toml`. SHAs are populated in Step 3 by running the fetcher in capture mode.

```toml
# Pinned versions of bundled pentest tools.
# Updating: bump version + url, then run `./scripts/fetch-tools.sh --capture-shas <platform>`
# to write the new SHA-256 values back into this file.

[nmap]
version = "7.95"
license = "Nmap Public Source License"
Darwin-arm64 = { url = "https://nmap.org/dist/nmap-7.95.dmg",                                                            sha256 = "" }
Linux-x86_64 = { url = "https://nmap.org/dist/nmap-7.95-1.x86_64.rpm",                                                  sha256 = "" }

[ffuf]
version = "2.1.0"
license = "MIT"
Darwin-arm64 = { url = "https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_macOS_arm64.tar.gz",            sha256 = "" }
Linux-x86_64 = { url = "https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_linux_amd64.tar.gz",            sha256 = "" }

[gobuster]
version = "3.6.0"
license = "Apache-2.0"
Darwin-arm64 = { url = "https://github.com/OJ/gobuster/releases/download/v3.6.0/gobuster_Darwin_arm64.tar.gz",           sha256 = "" }
Linux-x86_64 = { url = "https://github.com/OJ/gobuster/releases/download/v3.6.0/gobuster_Linux_x86_64.tar.gz",           sha256 = "" }

[nuclei]
version = "3.3.5"
license = "MIT"
Darwin-arm64 = { url = "https://github.com/projectdiscovery/nuclei/releases/download/v3.3.5/nuclei_3.3.5_macOS_arm64.zip", sha256 = "" }
Linux-x86_64 = { url = "https://github.com/projectdiscovery/nuclei/releases/download/v3.3.5/nuclei_3.3.5_linux_amd64.zip", sha256 = "" }

[playwright_chromium]
# Version managed by the playwright npm package in desktop/package.json.
# The fetcher invokes `npx playwright install chromium` rather than a URL.
managed_by = "playwright"
license = "BSD-3-Clause"
```

Note on nmap: as of the spec authoring date, nmap.org distributes a `.dmg` for macOS that contains an arm64-capable installer (Universal binary). The fetcher mounts it, copies the `nmap` binary out of the embedded package, and ejects. Linux nmap ships as a `.rpm` for the official prebuilt; we extract it via `rpm2cpio | cpio` rather than installing. **Verify these URLs are still live during Step 3** — if nmap.org changed packaging, update the table.

- [ ] **Step 2: Write fetch-tools.sh**

Create `scripts/fetch-tools.sh`:

```bash
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

# Inline TOML parsing via Python — bash can't read TOML natively.
read_field() {
  local section="$1" field="$2"
  python3 -c "
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
      # The .dmg contains a .mpkg installer. Use pkgutil --expand to crack it open
      # without root, then pull the nmap binary out of the payload.
      pkgutil --expand "$MOUNT_POINT"/nmap-*.mpkg/Contents/Packages/nmap*.pkg "$TMP/nmap-pkg"
      tar -xf "$TMP/nmap-pkg/Payload" -C "$TMP/nmap-payload"
      cp "$TMP/nmap-payload/usr/local/bin/nmap" "$OUT/nmap"
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

# --- ffuf (tar.gz, single binary at archive root) ---
fetch_and_extract_archive ffuf '
  tar -xzf "$archive" -C "$TMP" ffuf
  cp "$TMP/ffuf" "$OUT/ffuf"
'

# --- gobuster (tar.gz, single binary at archive root) ---
fetch_and_extract_archive gobuster '
  tar -xzf "$archive" -C "$TMP" gobuster
  cp "$TMP/gobuster" "$OUT/gobuster"
'

# --- nuclei (zip, single binary at archive root) ---
fetch_and_extract_archive nuclei '
  unzip -o "$archive" nuclei -d "$TMP" >/dev/null
  cp "$TMP/nuclei" "$OUT/nuclei"
'

# --- Playwright Chromium (Playwright manages its own download) ---
echo "[fetch-tools] playwright_chromium: invoking npx playwright install"
PLAYWRIGHT_BROWSERS_PATH="$OUT/playwright" \
  npx --prefix "$ROOT/desktop" playwright install chromium

# chmod everything in the bundle (no-op on Windows, executable on Unix)
chmod +x "$OUT"/nmap "$OUT"/ffuf "$OUT"/gobuster "$OUT"/nuclei 2>/dev/null || true

# Generate LICENSES.md
python3 - <<EOF > "$OUT/LICENSES.md"
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
```

Then:
```bash
chmod +x scripts/fetch-tools.sh
```

- [ ] **Step 3: Capture SHAs and populate the TOML**

Run: `./scripts/fetch-tools.sh --capture-shas $(uname -s)-$(uname -m)`
Expected: prints each tool's archive SHA next to its file path. Copy each SHA into the corresponding `sha256 = "..."` field in `scripts/tool-versions.toml`.

(If a URL 404s — e.g. nmap.org changed packaging — update the URL in `tool-versions.toml` and re-run.)

- [ ] **Step 4: Verify pinned SHAs**

Run: `./scripts/fetch-tools.sh $(uname -s)-$(uname -m)`
Expected: ends with `[fetch-tools] OK: <path>` and lists each tool binary plus `playwright/` and `LICENSES.md`.

Sanity-check the binaries:
```bash
./desktop/resources/tools/$(uname -s)-$(uname -m)/nmap --version | head -1
./desktop/resources/tools/$(uname -s)-$(uname -m)/ffuf -V | head -1
./desktop/resources/tools/$(uname -s)-$(uname -m)/gobuster version | head -1
./desktop/resources/tools/$(uname -s)-$(uname -m)/nuclei -version | head -1
```
Expected: each prints a version line. If any fails (`exec format error`, missing libs), the bundled binary doesn't match the host platform — check that the TOML's URL is right for this `<platform>`.

- [ ] **Step 5: Commit**

```bash
git add scripts/tool-versions.toml scripts/fetch-tools.sh
git commit -m "feat(packaging): tool fetcher + pinned versions for nmap/ffuf/gobuster/nuclei/chromium"
```

---

## Task 3: Electron supervisor — packaged-mode branch

**Files:**
- Modify: `desktop/electron/python.ts`
- Modify: `desktop/electron/main.ts`

- [ ] **Step 1: Add the platform identifier helper (TDD via the type-checker)**

Replace the contents of `desktop/electron/python.ts` so the file structure is:

(a) Existing imports + types stay.
(b) Add a `bundlePlatformTag()` helper.
(c) Rewrite `buildPythonEnv()` to take an `isPackaged` flag.
(d) Add `resolveSpawnCommand()`.
(e) Rewrite `PythonSupervisor.start()` to use `resolveSpawnCommand()`.
(f) Extend `defaultProjectRoot()` to branch on `app.isPackaged`.

Apply these edits. The full new file is:

```ts
import { spawn, ChildProcess, spawnSync } from "child_process";
import { app } from "electron";
import * as readline from "readline";
import path from "path";

export type Handshake = {
  type: "ready";
  port: number;
  token: string;
  pid: number;
  version: string;
};

export type SupervisorOptions = {
  /** Project root for the Python service (cwd). */
  projectRoot: string;
  /** Called with the handshake when the service is ready. */
  onReady: (h: Handshake) => void;
  /** Called when the service exits or fails to start. */
  onExit: (info: { code: number | null; signal: string | null; reason: string }) => void;
  /** Called for every stdout/stderr line after the handshake. */
  onLogLine: (line: string) => void;
};

/** Platform tag matching scripts/fetch-tools.sh's $(uname -s)-$(uname -m) output. */
function bundlePlatformTag(): string {
  const arch = process.arch === "x64" ? "x86_64" : process.arch;
  if (process.platform === "darwin") return `Darwin-${arch}`;
  if (process.platform === "linux") return `Linux-${arch}`;
  if (process.platform === "win32") return `Windows-${arch}`;
  return `${process.platform}-${arch}`;
}

/** Build the env handed to the spawned Python process.
 *
 *  Dev mode: prepend <projectRoot>/src to PYTHONPATH so `reverser` is
 *  importable without `pip install -e .`.
 *
 *  Packaged mode: drop PYTHONPATH (PyInstaller embeds the package). Append
 *  the bundled tools dir to PATH (system-first resolution — user's existing
 *  nmap wins). Set PLAYWRIGHT_BROWSERS_PATH to the bundled Chromium.
 */
function buildPythonEnv(projectRoot: string): NodeJS.ProcessEnv {
  const sep = process.platform === "win32" ? ";" : ":";

  if (app.isPackaged) {
    const platformTag = bundlePlatformTag();
    const toolsDir = path.join(process.resourcesPath, "tools", platformTag);
    const existingPath = process.env.PATH ?? "";
    const newPath = existingPath ? `${existingPath}${sep}${toolsDir}` : toolsDir;
    return {
      ...process.env,
      PATH: newPath,
      PLAYWRIGHT_BROWSERS_PATH: path.join(toolsDir, "playwright"),
    };
  }

  const srcDir = path.join(projectRoot, "src");
  const existing = process.env.PYTHONPATH ?? "";
  const pythonpath = existing ? `${srcDir}${sep}${existing}` : srcDir;
  return { ...process.env, PYTHONPATH: pythonpath };
}

/** Pick the first Python interpreter on PATH that can import the gui_service
 *  module. Dev-mode only. */
function findPython(projectRoot: string): { cmd: string; reason: string } | null {
  const env = buildPythonEnv(projectRoot);
  let lastReason = "";
  for (const cmd of ["python", "python3"]) {
    try {
      const r = spawnSync(cmd, ["-c", "import reverser.gui_service"], {
        cwd: projectRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
        encoding: "utf8",
      });
      if (r.status === 0) return { cmd, reason: "" };
      if (r.status !== null) {
        lastReason = `${cmd} -c "import reverser.gui_service" failed:\n${r.stderr.trim()}`;
      }
    } catch {
      // ENOENT — try the next candidate.
    }
  }
  return lastReason ? { cmd: "", reason: lastReason } : null;
}

/** Decide what command to spawn for the Python service. Branches on app.isPackaged. */
function resolveSpawnCommand(
  projectRoot: string
): { cmd: string; args: string[]; cwd: string } | { error: string } {
  const stdArgs = [
    "--host", "127.0.0.1",
    "--port", "0",
    "--project-root", projectRoot,
  ];

  if (app.isPackaged) {
    const exeName = process.platform === "win32"
      ? "reverser-service.exe"
      : "reverser-service";
    const cmd = path.join(
      process.resourcesPath, "python-dist", "reverser-service", exeName,
    );
    return { cmd, args: stdArgs, cwd: projectRoot };
  }

  const probe = findPython(projectRoot);
  if (!probe || !probe.cmd) {
    return {
      error: probe?.reason
        ?? "neither 'python' nor 'python3' is on PATH — run from inside `devenv shell`",
    };
  }
  return {
    cmd: probe.cmd,
    args: ["-u", "-m", "reverser.gui_service", ...stdArgs],
    cwd: projectRoot,
  };
}

export class PythonSupervisor {
  private proc: ChildProcess | null = null;
  private exited = false;

  constructor(private opts: SupervisorOptions) {}

  start(): void {
    if (this.proc) throw new Error("already started");

    const resolved = resolveSpawnCommand(this.opts.projectRoot);
    if ("error" in resolved) {
      this.exited = true;
      const reason = resolved.error;
      setImmediate(() => this.opts.onExit({ code: null, signal: null, reason }));
      return;
    }

    const proc = spawn(resolved.cmd, resolved.args, {
      cwd: resolved.cwd,
      env: buildPythonEnv(this.opts.projectRoot),
      stdio: ["ignore", "pipe", "pipe"],
    });

    this.proc = proc;

    const stdoutRl = readline.createInterface({ input: proc.stdout! });
    let handshakeSeen = false;
    const stderrBuf: string[] = [];

    stdoutRl.on("line", (line) => {
      if (!handshakeSeen) {
        try {
          const obj = JSON.parse(line) as Handshake;
          if (obj.type === "ready" && typeof obj.port === "number" && typeof obj.token === "string") {
            handshakeSeen = true;
            this.opts.onReady(obj);
            return;
          }
        } catch {
          // Not JSON — fall through to log forwarding.
        }
      }
      this.opts.onLogLine(line);
    });

    const stderrRl = readline.createInterface({ input: proc.stderr! });
    stderrRl.on("line", (line) => {
      if (!handshakeSeen) stderrBuf.push(line);
      this.opts.onLogLine(line);
    });

    proc.on("exit", (code, signal) => {
      if (this.exited) return;
      this.exited = true;
      const tail = stderrBuf.slice(-20).join("\n");
      const reason = handshakeSeen
        ? `service exited (code=${code}, signal=${signal})`
        : `service died before handshake (code=${code}, signal=${signal}) using ${resolved.cmd}` +
          (tail ? `\n\nstderr:\n${tail}` : "");
      this.opts.onExit({ code, signal, reason });
    });

    proc.on("error", (err) => {
      if (this.exited) return;
      this.exited = true;
      this.opts.onExit({ code: null, signal: null, reason: `spawn error: ${err.message}` });
    });
  }

  stop(): Promise<void> {
    const proc = this.proc;
    if (!proc || this.exited) return Promise.resolve();
    return new Promise((resolve) => {
      const killTimer = setTimeout(() => {
        try { proc.kill("SIGKILL"); } catch { /* already gone */ }
        resolve();
      }, 5000);
      proc.once("exit", () => {
        clearTimeout(killTimer);
        resolve();
      });
      try { proc.kill("SIGTERM"); } catch {
        clearTimeout(killTimer);
        resolve();
      }
    });
  }
}

/** Resolve the project root.
 *
 *  Dev mode: parent of the desktop/ dir (the repo root).
 *  Packaged mode: <userData>/project/. Created on first launch by main.ts.
 */
export function defaultProjectRoot(): string {
  if (app.isPackaged) {
    return path.join(app.getPath("userData"), "project");
  }
  return path.resolve(app.getAppPath(), "..");
}
```

- [ ] **Step 2: Verify the dev-mode regression suite still passes**

Run: `cd desktop && npx tsc --noEmit -p tsconfig.json`
Expected: exit 0.

Run: `pytest tests/ -q -k "not test_handshake_full_engagement_smoke and not test_handshake_full_endpoint_surface and not test_handshake_then_health"`
Expected: all pass (no Python-side change in this task; this just confirms no accidental regression).

- [ ] **Step 3: Add ensureProjectRootExists to main.ts**

In `desktop/electron/main.ts`, find the existing `startSupervisor()` function. Add a helper next to it:

```ts
async function ensureProjectRootExists(root: string): Promise<void> {
  await fs.mkdir(path.join(root, "targets"), { recursive: true });
}
```

`fs` and `path` are already imported at the top of the file (verify; if not, add `import path from "path"` and `import fs from "fs/promises"`).

Modify the function that calls `startSupervisor()` (look for `app.whenReady().then(async () => { startSupervisor(); ... })`). Insert an `await` to ensure-exist before the supervisor starts:

```ts
app.whenReady().then(async () => {
  await ensureProjectRootExists(defaultProjectRoot());
  startSupervisor();
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});
```

- [ ] **Step 4: Verify dev mode still works end-to-end**

Run: `cd desktop && npm run dev`

While it's running, in another terminal:
```bash
curl -s -H "Authorization: Bearer <token-from-log>" http://127.0.0.1:<port-from-log>/api/health | head -1
```
Expected: returns JSON with `"ok": true` (or similar). The supervisor change must not break dev mode.

Kill the dev server.

- [ ] **Step 5: Commit**

```bash
git add desktop/electron/python.ts desktop/electron/main.ts
git commit -m "feat(packaging): supervisor packaged-mode branch (PyInstaller binary + bundled tools PATH)"
```

---

## Task 4: electron-builder config + build-desktop.sh + sync-version.mjs

**Files:**
- Create: `desktop/electron-builder.yml`
- Create: `scripts/build-desktop.sh`
- Create: `scripts/sync-version.mjs`
- Modify: `desktop/package.json`

- [ ] **Step 1: Update desktop/package.json**

Add `electron-builder` and `electron-updater` to `devDependencies`, and add a `dist` script:

Edit `desktop/package.json` so the `devDependencies` section includes the two new packages (alphabetically among existing entries) and a new `scripts.dist`:

```json
{
  "name": "reverser-desktop",
  "private": true,
  "version": "0.1.0",
  "description": "Electron desktop UI for reverser",
  "main": "dist-electron/main.js",
  "scripts": {
    "predev": "tsc -p tsconfig.electron.json",
    "dev": "vite",
    "build": "tsc -p tsconfig.electron.json && tsc -b && vite build",
    "preview": "vite preview",
    "lint": "tsc -b --noEmit",
    "test:e2e": "playwright test",
    "dist": "electron-builder"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "electron-updater": "^6.3.0",
    "lucide-react": "^0.456.0",
    "react": "^18.3.1",
    "react-arborist": "^3.6.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.1.0",
    "react-resizable-panels": "^2.1.9",
    "react-router-dom": "^6.27.0",
    "remark-gfm": "^4.0.1",
    "tailwind-merge": "^2.5.4",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "@types/node": "^22.7.0",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "autoprefixer": "^10.4.20",
    "electron": "^32.2.0",
    "electron-builder": "^25.1.8",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.14",
    "typescript": "^5.6.3",
    "vite": "^5.4.10",
    "vite-plugin-electron": "^0.28.8",
    "vite-plugin-electron-renderer": "^0.14.6"
  }
}
```

Then run `cd desktop && npm install` to update `package-lock.json`. Expected: no errors; `node_modules/.bin/electron-builder` exists.

- [ ] **Step 2: Write desktop/electron-builder.yml**

Create `desktop/electron-builder.yml`:

```yaml
# electron-builder config. Reads desktop/package.json's "version" field
# (synced from pyproject.toml by scripts/sync-version.mjs).
appId: dev.warthog-trout.reverser
productName: reverser
copyright: Copyright © 2026 John Rizzo

# We don't use asar — Python service is outside the Electron app entrypoint
# anyway, and unpacked code is simpler to debug at install sites.
asar: false

# Where electron-builder finds the renderer output and the main.js bundle.
files:
  - "dist/**/*"
  - "dist-electron/**/*"
  - "package.json"

# Bundle the PyInstaller output (same path on every platform, since CI
# matrix runs one platform per job).
extraResources:
  - from: "python-dist/reverser-service"
    to: "python-dist/reverser-service"
    filter: ["**/*"]

# Output dir for installers.
directories:
  output: "dist"

# macOS-specific
mac:
  target:
    - target: dmg
      arch: [arm64]
  category: public.app-category.developer-tools
  # Signing/notarization are skipped automatically when these env vars are unset.
  # When set in CI, electron-builder picks them up:
  #   CSC_LINK, CSC_KEY_PASSWORD              (signing identity)
  #   APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID  (notarization)
  hardenedRuntime: true
  entitlements: build/entitlements.mac.plist
  entitlementsInherit: build/entitlements.mac.plist
  extraResources:
    - from: "resources/tools/Darwin-arm64"
      to: "tools/Darwin-arm64"
      filter: ["**/*"]

dmg:
  sign: false
  contents:
    - x: 130
      y: 220
    - x: 410
      y: 220
      type: link
      path: /Applications

# Linux-specific
linux:
  target:
    - target: AppImage
      arch: [x64]
  category: Development
  extraResources:
    - from: "resources/tools/Linux-x86_64"
      to: "tools/Linux-x86_64"
      filter: ["**/*"]

# Auto-update metadata. electron-builder generates latest-mac.yml / latest-linux.yml
# alongside the installer when `publish` is configured.
publish:
  - provider: github
    owner: johnrizzo1
    repo: reverser
    releaseType: release
```

Now create `desktop/build/entitlements.mac.plist` (needed when `hardenedRuntime: true` is set, even without signing):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key>
  <true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key>
  <true/>
  <key>com.apple.security.cs.disable-library-validation</key>
  <true/>
  <key>com.apple.security.network.client</key>
  <true/>
  <key>com.apple.security.network.server</key>
  <true/>
</dict>
</plist>
```

- [ ] **Step 3: Write scripts/sync-version.mjs**

Create `scripts/sync-version.mjs`:

```javascript
#!/usr/bin/env node
// Read [project].version from pyproject.toml and write it to
// desktop/package.json's "version" field. Runs before electron-builder.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pyprojectPath = path.join(root, "pyproject.toml");
const packageJsonPath = path.join(root, "desktop", "package.json");

const pyproject = fs.readFileSync(pyprojectPath, "utf-8");
// Minimal TOML parsing for the [project] version field — avoid pulling in a TOML dep.
const m = pyproject.match(/^\[project\][^\[]*?\bversion\s*=\s*"([^"]+)"/m);
if (!m) {
  console.error("[sync-version] FAIL: could not find [project] version in pyproject.toml");
  process.exit(1);
}
const version = m[1];

const pkg = JSON.parse(fs.readFileSync(packageJsonPath, "utf-8"));
if (pkg.version === version) {
  console.log(`[sync-version] desktop/package.json already at ${version}`);
  process.exit(0);
}
pkg.version = version;
fs.writeFileSync(packageJsonPath, JSON.stringify(pkg, null, 2) + "\n");
console.log(`[sync-version] desktop/package.json -> ${version}`);
```

Then:
```bash
chmod +x scripts/sync-version.mjs
node scripts/sync-version.mjs
```
Expected: prints `[sync-version] desktop/package.json already at 0.1.0` (since both files start at 0.1.0).

- [ ] **Step 4: Write scripts/build-desktop.sh**

Create `scripts/build-desktop.sh`:

```bash
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
```

Then:
```bash
chmod +x scripts/build-desktop.sh
```

- [ ] **Step 5: Commit**

```bash
git add desktop/electron-builder.yml desktop/build/entitlements.mac.plist desktop/package.json desktop/package-lock.json scripts/build-desktop.sh scripts/sync-version.mjs
git commit -m "feat(packaging): electron-builder config + build-desktop.sh + sync-version.mjs"
```

---

## Task 5: devenv.nix package scripts + first local end-to-end build

**Files:**
- Modify: `devenv.nix`

- [ ] **Step 1: Add the package scripts to devenv.nix**

Find the existing `scripts.harness-init.exec = ''` block (around line 209). Append two new scripts in the same style:

```nix
scripts.package.exec = ''
  set -euo pipefail
  cd "${config.devenv.root}"
  ./scripts/fetch-tools.sh "$(uname -s)-$(uname -m)"
  ./scripts/build-python.sh
  ./scripts/build-desktop.sh
'';

scripts.package-installer.exec = ''
  set -euo pipefail
  cd "${config.devenv.root}"
  ./scripts/fetch-tools.sh "$(uname -s)-$(uname -m)"
  ./scripts/build-python.sh
  ./scripts/build-desktop.sh --installer
'';
```

- [ ] **Step 2: Run the unpacked build end-to-end**

Inside `devenv shell` (or after `direnv allow`):

Run: `package`
Expected: ends with `[build-desktop] OK: artifacts in desktop/dist/` and lists either `mac-arm64/` (macOS) or `linux-unpacked/` (Linux). Total runtime: ~3–5 minutes on the first run.

If any sub-script fails:
- `[fetch-tools] FAIL: SHA mismatch` → re-run `./scripts/fetch-tools.sh --capture-shas <platform>`, update the TOML.
- `[build-python] FAIL` → check the PyInstaller spec; add missing `hiddenimports`.
- `[build-desktop] FAIL` → check `desktop/dist/` for half-written output; `rm -rf` and retry.

- [ ] **Step 3: Launch the unpacked bundle and verify**

On macOS:
```bash
open desktop/dist/mac-arm64/reverser.app
```

On Linux:
```bash
desktop/dist/linux-unpacked/reverser
```

Expected:
- App window opens with the Dashboard.
- Profile cards appear within 15s (the bundled Python service handshakes, the renderer fetches `/api/profiles`).
- No "Backend service exited" error banner.
- Bundled tools work: open DevTools (View → Toggle Developer Tools), run in the Console:
  ```js
  // Replace <port> + <token> from the connection log
  fetch("http://127.0.0.1:<port>/api/health", { headers: { Authorization: "Bearer <token>" }})
    .then(r => r.json()).then(console.log)
  ```
  The health response should include `"nmap": {"ok": true, "detail": "..."}`.

Quit the app.

- [ ] **Step 4: Run the installer build**

Run: `package-installer`
Expected: same as Step 2 but additionally produces `desktop/dist/reverser-0.1.0-arm64.dmg` (macOS) or `desktop/dist/reverser-0.1.0.AppImage` (Linux), plus `latest-mac.yml` / `latest-linux.yml`.

On macOS, double-click the `.dmg` and drag the app to `/Applications`. Open `/Applications/reverser.app`. If Gatekeeper blocks it: `xattr -d com.apple.quarantine /Applications/reverser.app` then re-open. Verify the same handshake + profiles as Step 3.

On Linux: `chmod +x desktop/dist/*.AppImage && desktop/dist/*.AppImage`. Same verification.

- [ ] **Step 5: Commit**

```bash
git add devenv.nix scripts/tool-versions.toml
git commit -m "feat(packaging): devenv package + package-installer scripts; pin tool SHAs"
```

(Tool SHAs were populated during Task 2 Step 3, but if they're still unset re-run that step now.)

---

## Task 6: Packaged smoke test

**Files:**
- Create: `desktop/tests/packaged/smoke.spec.ts`
- Modify: `desktop/renderer/src/state/connection.ts` (expose store on `window` for tests)
- Modify: `src/reverser/gui_service/routes/health.py` (add bundled-tool checks)

- [ ] **Step 1: Add bundled-tool checks to /api/health**

The existing `_build_checks()` returns `{python, devenv_shell, playwright_chromium, msf_rpcd, neo4j}`. The smoke test asserts `health.checks.nmap.ok` and we need that to be a real check.

Edit `src/reverser/gui_service/routes/health.py`. Find `_build_checks()` (around line 70) and add the four bundled binaries:

```python
def _build_checks() -> dict:
    return {
        "python": _check_python(),
        "devenv_shell": _check_devenv_shell(),
        "playwright_chromium": _check_playwright_chromium(),
        "msf_rpcd": _check_binary_on_path("msfrpcd", "Metasploit RPC daemon"),
        "neo4j": _check_binary_on_path("neo4j", "Neo4j"),
        # Tier-1 bundled tools — these probe PATH, so they hit the bundled
        # binary when no system one exists (packaged app) and the system
        # binary otherwise.
        "nmap": _check_binary_on_path("nmap", "Nmap"),
        "ffuf": _check_binary_on_path("ffuf", "ffuf"),
        "gobuster": _check_binary_on_path("gobuster", "gobuster"),
        "nuclei": _check_binary_on_path("nuclei", "nuclei"),
    }
```

Verify with the existing tests:

Run: `pytest tests/gui_service/test_health.py -v`
Expected: all existing health tests still pass (they only check that listed keys exist and have `ok`+`detail`, not that the dict is exactly this size).

- [ ] **Step 2: Expose the connection store on `window` for test access**

Edit `desktop/renderer/src/state/connection.ts`. After the `useConnection` export, add:

```ts
// Test-only handle: expose the store on `window` so Playwright e2e specs can
// read the live port + token without parsing UI text. Cheap and harmless in
// production (it's just a reference to the existing store).
if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__reverserConnection = useConnection;
}
```

- [ ] **Step 3: Write the smoke test**

Create `desktop/tests/packaged/smoke.spec.ts`:

```typescript
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";
import fs from "fs";

// Smoke test for the packaged installer. Launches the built .app/.AppImage
// (NOT the source tree) and asserts the Python service handshakes and the
// bundled tools are on PATH.

function packagedBinary(): string {
  const dist = path.join(__dirname, "..", "..", "dist");
  if (process.platform === "darwin") {
    return path.join(dist, "mac-arm64", "reverser.app", "Contents", "MacOS", "reverser");
  }
  if (process.platform === "linux") {
    return path.join(dist, "linux-unpacked", "reverser");
  }
  throw new Error(`unsupported platform: ${process.platform}`);
}

test("packaged app launches and dashboard renders", async () => {
  const exe = packagedBinary();
  if (!fs.existsSync(exe)) {
    test.fail(true, `packaged binary not found at ${exe} — run ./scripts/build-desktop.sh first`);
  }
  const app = await electron.launch({ executablePath: exe, args: [] });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 60_000 });
    const cards = w.locator(".grid > div");
    await expect(async () => {
      const count = await cards.count();
      expect(count).toBeGreaterThanOrEqual(10);
    }).toPass({ timeout: 60_000 });
  } finally {
    await app.close();
  }
});

test("packaged app finds bundled nmap on PATH", async () => {
  const exe = packagedBinary();
  if (!fs.existsSync(exe)) {
    test.fail(true, `packaged binary not found at ${exe} — run ./scripts/build-desktop.sh first`);
  }
  const app = await electron.launch({ executablePath: exe, args: [] });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 60_000 });

    // Read the connection info from the zustand store exposed on window by
    // desktop/renderer/src/state/connection.ts (test-only handle).
    const conn = await w.evaluate(() => {
      const store = (window as unknown as {
        __reverserConnection?: { getState: () => { port: number; token: string } };
      }).__reverserConnection;
      if (!store) return null;
      const s = store.getState();
      return { port: s.port, token: s.token };
    });
    expect(conn, "renderer must expose connection info").not.toBeNull();
    expect(conn!.port, "port must be set").toBeTruthy();

    // Hit /api/health from inside the renderer (CSP allows localhost).
    const health = await w.evaluate(async (c: { port: number; token: string }) => {
      const res = await fetch(`http://127.0.0.1:${c.port}/api/health`, {
        headers: { Authorization: `Bearer ${c.token}` },
      });
      return res.json();
    }, conn!);

    expect(health.checks.nmap.ok, "nmap should be reachable").toBe(true);
  } finally {
    await app.close();
  }
});
```

The first test re-uses the existing dashboard-renders assertion pattern (matches `desktop/tests/e2e/smoke.spec.ts`). The second test exercises the bundled-PATH change from Task 3 by verifying `/api/health` reports nmap as available.

- [ ] **Step 4: Rebuild the renderer + repackage so the new window export is in the installer**

The window export is in the renderer bundle, and the health-endpoint change is in the Python service, so a fresh `package` (Task 5) is needed for the smoke test to find them.

Run: `package`
Expected: completes successfully.

- [ ] **Step 5: Run the smoke test locally**

Run: `cd desktop && npx playwright test tests/packaged/smoke.spec.ts`
Expected: 2 passed in ~90s.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/health.py desktop/renderer/src/state/connection.ts desktop/tests/packaged/smoke.spec.ts
git commit -m "test(packaging): smoke test for packaged app + bundled nmap on PATH"
```

---

## Task 7: GitHub Actions build matrix

**Files:**
- Create: `.github/workflows/build.yml`

- [ ] **Step 1: Write the build workflow**

Create `.github/workflows/build.yml`:

```yaml
name: Build

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    name: Build (${{ matrix.os }} / ${{ matrix.platform_tag }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: macos-latest
            platform_tag: Darwin-arm64
            artifact_glob: "desktop/dist/*.dmg"
          - os: ubuntu-latest
            platform_tag: Linux-x86_64
            artifact_glob: "desktop/dist/*.AppImage"
          # Future: { os: windows-latest, platform_tag: Windows-x86_64, artifact_glob: "desktop/dist/*.exe" }

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: desktop/package-lock.json

      - name: Install Python deps + PyInstaller
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]" pyinstaller

      - name: Fetch bundled tools
        run: ./scripts/fetch-tools.sh ${{ matrix.platform_tag }}

      - name: Cache PyInstaller intermediate build
        uses: actions/cache@v4
        with:
          path: desktop/build
          key: pyinstaller-${{ runner.os }}-${{ hashFiles('desktop/reverser-service.spec', 'pyproject.toml') }}

      - name: Build Python service (PyInstaller)
        run: ./scripts/build-python.sh

      - name: Build desktop (electron-builder installer)
        env:
          # Signing env vars are intentionally unset in v1 — electron-builder
          # skips signing automatically. Set these as repo secrets to enable.
          CSC_LINK: ${{ secrets.CSC_LINK }}
          CSC_KEY_PASSWORD: ${{ secrets.CSC_KEY_PASSWORD }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_SPECIFIC_PASSWORD: ${{ secrets.APPLE_APP_SPECIFIC_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
          # electron-builder reads GH_TOKEN to upload to GitHub Releases.
          # We use --publish never here (PR/main builds shouldn't publish).
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: ./scripts/build-desktop.sh --installer

      - name: Run packaged smoke test
        run: cd desktop && npx playwright install --with-deps chromium && npx playwright test tests/packaged/smoke.spec.ts

      - name: Upload installer artifact
        uses: actions/upload-artifact@v4
        with:
          name: reverser-${{ matrix.platform_tag }}
          path: |
            ${{ matrix.artifact_glob }}
            desktop/dist/latest-*.yml
          if-no-files-found: error
          retention-days: 14
```

- [ ] **Step 2: Push and verify the matrix passes on a PR**

```bash
git add .github/workflows/build.yml
git commit -m "ci(packaging): matrix build workflow for macOS + Linux installers"
git push -u origin main   # or a feature branch + open a PR
```

Then watch GitHub Actions. Expected: both matrix jobs pass within ~20 minutes. If a job fails, the artifact upload step won't run; debug the failing step before continuing.

Common first-time failures:
- `fetch-tools.sh` SHA mismatch on the runner (different platform). You captured SHAs locally on one platform; the other platform's SHAs must be captured on a runner with that OS. The simplest fix: open a draft PR, let CI fail on the missing platform's SHAs, copy the actual SHAs from the CI log into `tool-versions.toml`, push again.
- PyInstaller missing import on a runner: add to `hiddenimports` in `reverser-service.spec`.
- Playwright Chromium download fails: usually a transient network issue; re-run the job.

- [ ] **Step 3: Commit any iterations**

If you had to add hidden imports or update SHAs:
```bash
git add scripts/tool-versions.toml desktop/reverser-service.spec
git commit -m "fix(packaging): CI-discovered tool SHAs + hidden imports"
git push
```

---

## Task 8: GitHub Actions release workflow + gitea mirror

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*.*.*"]
  workflow_dispatch:
    inputs:
      tag:
        description: "Tag to release (must already exist)"
        required: true

jobs:
  build:
    # Reuse the matrix build by uploading artifacts on tag pushes too.
    uses: ./.github/workflows/build.yml

  publish:
    name: Publish GitHub Release + mirror to gitea
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Download all artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts/

      - name: Flatten artifact directories
        run: |
          mkdir -p release/
          find artifacts/ -type f \( -name "*.dmg" -o -name "*.AppImage" -o -name "latest-*.yml" \) \
            -exec cp -v {} release/ \;
          ls -la release/

      - name: Determine tag
        id: tag
        run: |
          if [ -n "${{ github.event.inputs.tag }}" ]; then
            echo "tag=${{ github.event.inputs.tag }}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=${GITHUB_REF#refs/tags/}" >> "$GITHUB_OUTPUT"
          fi

      - name: Publish to GitHub Releases
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ steps.tag.outputs.tag }}
          files: release/*
          generate_release_notes: true

      - name: Mirror to gitea
        env:
          GITEA_TOKEN: ${{ secrets.GITEA_TOKEN }}
          GITEA_URL: https://gitea.warthog-trout.ts.net
          TAG: ${{ steps.tag.outputs.tag }}
        run: |
          if [ -z "$GITEA_TOKEN" ]; then
            echo "::warning::GITEA_TOKEN not set; skipping gitea mirror"
            exit 0
          fi
          set -euo pipefail

          # Create the release on gitea (it tolerates 409 if already exists)
          RELEASE_JSON=$(curl -fsSL -X POST \
            "$GITEA_URL/api/v1/repos/johnrizzo1/reverser/releases" \
            -H "Authorization: token $GITEA_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"tag_name\":\"$TAG\",\"name\":\"$TAG\",\"draft\":false,\"prerelease\":false}" \
            || curl -fsSL "$GITEA_URL/api/v1/repos/johnrizzo1/reverser/releases/tags/$TAG" \
              -H "Authorization: token $GITEA_TOKEN")

          RELEASE_ID=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
          echo "gitea release id: $RELEASE_ID"

          # Upload each artifact as a release asset
          for f in release/*; do
            echo "uploading $(basename "$f")..."
            curl -fsSL -X POST \
              "$GITEA_URL/api/v1/repos/johnrizzo1/reverser/releases/$RELEASE_ID/assets?name=$(basename "$f")" \
              -H "Authorization: token $GITEA_TOKEN" \
              -F "attachment=@$f"
            echo
          done
          echo "mirror complete"
```

The `build` job uses `uses: ./.github/workflows/build.yml` to invoke the existing workflow as a reusable workflow. For this to work, `build.yml`'s `on:` block must include `workflow_call:` — Step 2 below adds it.

- [ ] **Step 2: Update build.yml so release.yml can call it**

Edit `.github/workflows/build.yml`'s `on:` block (lines 3-7) to add `workflow_call:`:

```yaml
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
  workflow_call:
```

- [ ] **Step 3: Add the GITEA_TOKEN secret**

Manually (one time, not in CI):
1. Go to gitea → User Settings → Applications → Generate New Token. Scopes: `write:repository`. Copy.
2. Go to GitHub → repo Settings → Secrets and variables → Actions → New repository secret. Name: `GITEA_TOKEN`. Value: paste.

- [ ] **Step 4: Tag a dry-run release**

```bash
git add .github/workflows/release.yml .github/workflows/build.yml
git commit -m "ci(packaging): release workflow with gitea mirror"
git push

# Bump to a pre-release version for the dry run
# (edit pyproject.toml -> version = "0.1.0-rc.1")
git add pyproject.toml
git commit -m "chore: bump to 0.1.0-rc.1 for packaging dry run"
git tag v0.1.0-rc.1
git push origin v0.1.0-rc.1
```

Expected: GitHub Actions runs `release.yml`, which runs the build matrix then the publish job. Within ~25 min:
- A new GitHub Release `v0.1.0-rc.1` appears with `.dmg`, `.AppImage`, `latest-mac.yml`, `latest-linux.yml` attached.
- A corresponding release appears on gitea with the same artifacts.

If the publish step fails:
- Check `GITHUB_TOKEN` permissions: the workflow's `permissions: contents: write` block (line `permissions:` in `release.yml`) is required.
- Check `GITEA_TOKEN`: if absent, the mirror step prints a warning and exits 0 (release is still on GitHub).

- [ ] **Step 5: Commit any iterations**

If you had to fix anything:
```bash
git add .github/workflows/release.yml
git commit -m "fix(ci): release workflow tweaks"
git push
```

---

## Task 9: Documentation — README install instructions + release checklist

**Files:**
- Create: `docs/release-checklist.md`
- Modify: `README.md`

- [ ] **Step 1: Write the release checklist**

Create `docs/release-checklist.md`:

```markdown
# Release checklist

Run through this before tagging a release. Each item must pass on each
supported platform (macOS arm64, Linux x64).

## Pre-release

- [ ] All open PRs for the milestone are merged.
- [ ] `pytest tests/ -q` passes on `main` (excluding the pre-existing
      `test_handshake_*` env failures).
- [ ] `cd desktop && npm run lint` passes.
- [ ] CI build matrix is green on the latest commit on `main`.
- [ ] CHANGELOG / release notes drafted (if applicable).
- [ ] `pyproject.toml` version bumped.

## Per-platform manual verification

After running `package-installer` (or downloading a CI artifact):

### macOS arm64

- [ ] Installer (`.dmg`) opens; Applications icon visible; drag-to-install works.
- [ ] First launch (without removing quarantine): see the Gatekeeper dialog.
      Run `xattr -d com.apple.quarantine /Applications/reverser.app`. Re-open.
- [ ] App window appears; Dashboard view loads; ≥10 profile cards visible
      within 30 seconds.
- [ ] Settings → Health: every check shows OK (Python service, nmap,
      Playwright Chromium).
- [ ] Create a new engagement using the `general` profile, type a message,
      receive an agent response. (Full handshake + agent loop.)
- [ ] Switch to the `webpentest` profile, create an engagement, verify
      Playwright Chromium launches (the agent emits a screenshot or page
      title).
- [ ] Stop the engagement; verify the snapshot persists in
      `~/Library/Application Support/reverser/project/targets/<target>/`.

### Linux x64

- [ ] `chmod +x reverser-*.AppImage && ./reverser-*.AppImage` launches.
- [ ] App window appears; Dashboard view loads; ≥10 profile cards visible.
- [ ] Settings → Health: same OK checks as macOS.
- [ ] Same `general` profile end-to-end test.
- [ ] Same `webpentest` profile test.
- [ ] Snapshot persists in `~/.config/reverser/project/targets/<target>/`.

## Tag the release

- [ ] `git tag v<version> && git push origin v<version>`
- [ ] Release workflow finishes successfully on GitHub Actions.
- [ ] GitHub Release appears with both `.dmg` and `.AppImage` plus
      `latest-mac.yml` and `latest-linux.yml`.
- [ ] gitea mirror release appears with the same assets.
```

- [ ] **Step 2: Add install + dev instructions to README.md**

Read the existing README to find the right insertion point (likely after a "Quick start" or "Setup" section). Add a new section:

```markdown
## Installing

### Pre-built installers

Download the latest installer for your platform from
[GitHub Releases](https://github.com/johnrizzo1/reverser/releases) or the
[gitea mirror](https://gitea.warthog-trout.ts.net/johnrizzo1/reverser/releases):

- **macOS (Apple Silicon):** `reverser-<version>-arm64.dmg`. After
  installing, run once to clear the quarantine flag (unsigned builds
  trigger Gatekeeper):
  ```bash
  xattr -d com.apple.quarantine /Applications/reverser.app
  ```
- **Linux x86_64:** `reverser-<version>.AppImage`. Make it executable
  and run:
  ```bash
  chmod +x reverser-*.AppImage
  ./reverser-*.AppImage
  ```

The installer bundles:
- The Python backend (FastAPI + pentest agent), packaged with PyInstaller.
- `nmap`, `ffuf`, `gobuster`, `nuclei`, and Playwright Chromium for
  out-of-the-box pentest profile coverage.

Bundled tools are used as a fallback. If you already have these on your
`PATH` (e.g. NixOS, Homebrew), your version is preferred.

### Building from source

Requires [devenv](https://devenv.sh):

```bash
devenv shell
package-installer    # produces desktop/dist/reverser-<version>-<arch>.{dmg,AppImage}
```

`package` (without the `-installer` suffix) produces an unpacked bundle
under `desktop/dist/{mac-arm64,linux-unpacked}/` for fast iteration.
```

- [ ] **Step 3: Commit**

```bash
git add docs/release-checklist.md README.md
git commit -m "docs(packaging): release checklist + install instructions in README"
```

---

## Self-review notes

Run before considering the plan complete:

- [ ] All five tool binaries (`nmap`, `ffuf`, `gobuster`, `nuclei`, Chromium) appear in `desktop/resources/tools/<platform>/` after Task 2 Step 4.
- [ ] `./scripts/build-python.sh` produces a working `reverser-service` binary that handshakes (Task 1 Step 4).
- [ ] `package` produces an unpacked bundle that launches and shows the Dashboard (Task 5 Step 3).
- [ ] `package-installer` produces a working `.dmg` (macOS) or `.AppImage` (Linux) (Task 5 Step 4).
- [ ] `desktop/tests/packaged/smoke.spec.ts` passes locally (Task 6 Step 2).
- [ ] CI matrix is green on a feature-branch PR (Task 7 Step 2).
- [ ] A dry-run `v0.1.0-rc.1` tag publishes to both GitHub Releases and gitea (Task 8 Step 4).
- [ ] README and release checklist are present (Task 9).

If any item above is unchecked, the corresponding task has unresolved work.
