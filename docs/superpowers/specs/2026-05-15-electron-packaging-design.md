# Electron Packaging — Design Spec

**Date:** 2026-05-15
**Status:** Approved for planning
**Scope:** Production-ready installers for the Electron desktop app on macOS (arm64) and Linux (x64), with bundled `nmap`, Playwright Chromium, `ffuf`, `gobuster`, `nuclei`. Auto-update metadata published; in-app update prompt deferred. Code signing wired but disabled. Project B (in-app tool manager) deferred to a separate spec.

## 1. Goals & non-goals

### Goals

- Produce a single-file `.dmg` for macOS (arm64) and `.AppImage` for Linux (x64) per release.
- Bundle the Python backend (FastAPI service) into the installer via PyInstaller `--onedir` so end users don't need Python or `pip install`.
- Bundle a curated set of pentest tools (`nmap`, Playwright Chromium, `ffuf`, `gobuster`, `nuclei`) so the app is useful out of the box without requiring NixOS/Homebrew tool curation.
- Resolve tools system-first: a user's existing `nmap` on PATH wins over the bundled one, so curated installs (NixOS, Homebrew) aren't overridden.
- Publish to GitHub Releases and mirror to the project's gitea instance on every tagged release.
- Auto-update metadata (`latest.yml`, `latest-mac.yml`, `latest-linux.yml`) published alongside artifacts so `electron-updater` works in v1.1 without rework.
- Reuse the same build commands locally (via devenv) and in CI.

### Non-goals (this phase)

- In-app tool manager / Tier-2 downloads (Project B — separate spec).
- Windows builds — CI hook reserved, no builds run.
- macOS Intel (x64) — dropped; arm64 only.
- Linux `.deb` / `.rpm` — AppImage only.
- Code signing certs purchased — env vars wired, v1 ships unsigned.
- In-app "update available" notification — `autoUpdater.checkForUpdates()` call deferred to v1.1; the v1 build publishes the metadata needed for that future call.
- arm64 Linux.
- Tool version upgrades from inside the app.

## 2. Architecture

### Runtime file layout (inside the installed app)

```
<app-bundle>/
├── Electron app                       (renderer, main process, preload)
└── resources/                         ← electron-builder's `extraResources`
    ├── python-dist/
    │   └── reverser-service/          ← PyInstaller --onedir output
    │       ├── reverser-service[.exe] ← entry binary
    │       ├── _internal/             ← Python interpreter + frozen deps
    │       └── ...
    └── tools/
        ├── nmap
        ├── ffuf
        ├── gobuster
        ├── nuclei
        ├── playwright/                ← Chromium download tree
        │   └── chromium-NNNN/...
        └── LICENSES.md                ← generated at build time
```

`process.resourcesPath` (electron-builder convention) points at `resources/` at runtime.

### Runtime flow

1. Electron main starts. `app.isPackaged === true`.
2. `PythonSupervisor.start()` resolves the spawn command via `resolveSpawnCommand()` — returns `<resourcesPath>/python-dist/reverser-service/reverser-service` plus the standard `--host 127.0.0.1 --port 0 --project-root <userDataProject>` args.
3. Supervisor calls `buildPythonEnv()`, which:
   - Appends `<resourcesPath>/tools/<platform>/` to `PATH` (system-first → bundled fallback).
   - Sets `PLAYWRIGHT_BROWSERS_PATH=<resourcesPath>/tools/<platform>/playwright`.
   - Drops the dev-mode `PYTHONPATH=<root>/src` prepend — PyInstaller embeds the package.
4. `spawn()` launches the binary. Handshake JSON on stdout — unchanged from current.
5. Profiles call `subprocess.run(["nmap", ...])` as today; the bundled binary is found via the appended PATH entry when no system one exists.

### Dev mode is preserved

When `app.isPackaged === false`, `resolveSpawnCommand()` returns the existing dev-mode tuple: `python3 -m reverser.gui_service` with `PYTHONPATH=<root>/src` and no tool-bundle PATH manipulation. Developers running `npm run dev` see no behavior change.

### `projectRoot` semantics

- **Dev mode:** `path.resolve(app.getAppPath(), "..")` — the repo root.
- **Packaged mode:** `path.join(app.getPath("userData"), "project")`. Default location:
  - macOS: `~/Library/Application Support/reverser/project/`
  - Linux: `~/.config/reverser/project/`

  First launch creates the dir and an empty `targets/`. `.reverser-authorized` and logs live here too.

## 3. Build pipeline

### CI matrix (GitHub Actions)

Two jobs in v1: `macos-latest` (arm64) and `ubuntu-26.04` (or `ubuntu-latest` until 26.04 lands as a runner image). The matrix is structured so a `windows-latest` entry is one line away when Windows support resumes.

### Per-job steps

1. Checkout.
2. Run `./scripts/fetch-tools.sh <platform>` — downloads pinned tool binaries into `desktop/resources/tools/<platform>/`. Pulls Playwright Chromium via `npx playwright install chromium` against a temporary `PLAYWRIGHT_BROWSERS_PATH`.
3. `actions/setup-python@v5` (Python 3.12) + `actions/setup-node@v4` (Node 20). Cache pip and npm.
4. `pip install -e ".[dev]" pyinstaller`.
5. `./scripts/build-python.sh` — wraps `pyinstaller desktop/reverser-service.spec`. Output: `desktop/python-dist/<platform>/reverser-service/`.
6. `./scripts/build-desktop.sh --installer` — wraps `cd desktop && npm ci && npm run build && npx electron-builder`. Reads `desktop/electron-builder.yml`, bundles `python-dist/` and `resources/tools/` via `extraResources`, produces the installer in `desktop/dist/`.
7. `actions/upload-artifact@v4` uploads the installer.

PR builds run steps 1–6 without publishing. Release builds (tag `v*.*.*`) trigger a separate `release` job.

### Release job (tag-only)

Runs after the matrix completes on `v*.*.*` tags:

1. Downloads both platform artifacts.
2. Publishes to GitHub Releases (uses `softprops/action-gh-release@v2` or equivalent).
3. Generates `latest.yml` (Windows, future), `latest-mac.yml`, `latest-linux.yml` via `electron-builder`'s publish step (re-invoked with `--publish always` against the downloaded artifacts).
4. Mirrors all artifacts + metadata to gitea via `gh api` against gitea's GitHub-compatible Releases endpoint (`https://gitea.warthog-trout.ts.net/api/v1/repos/johnrizzo1/reverser/releases`).

### Code signing (off in v1)

`electron-builder.yml` references the standard env vars: `CSC_LINK`, `CSC_KEY_PASSWORD` (macOS), `WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD` (Windows, future). When unset, electron-builder skips signing and emits a warning — the build succeeds. Notarization vars (`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`) likewise gate notarization off when unset. Turning signing on later is purely operational: set the secrets in the GitHub repo's Actions config.

## 4. Tool bundle structure

### `scripts/tool-versions.toml` (single source of truth)

```toml
[nmap]
version = "7.95"
macos_arm64 = { url = "https://nmap.org/dist/nmap-7.95.dmg", sha256 = "..." }
linux_x64   = { url = "https://nmap.org/dist/nmap-7.95.tgz", sha256 = "..." }

[ffuf]
version = "2.1.0"
macos_arm64 = { url = "https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_macOS_arm64.tar.gz", sha256 = "..." }
linux_x64   = { url = "https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_linux_amd64.tar.gz", sha256 = "..." }

[gobuster]
version = "3.6.0"
macos_arm64 = { url = "https://github.com/OJ/gobuster/releases/download/v3.6.0/gobuster_Darwin_arm64.tar.gz", sha256 = "..." }
linux_x64   = { url = "https://github.com/OJ/gobuster/releases/download/v3.6.0/gobuster_Linux_x86_64.tar.gz", sha256 = "..." }

[nuclei]
version = "3.3.5"
macos_arm64 = { url = "https://github.com/projectdiscovery/nuclei/releases/download/v3.3.5/nuclei_3.3.5_macOS_arm64.zip", sha256 = "..." }
linux_x64   = { url = "https://github.com/projectdiscovery/nuclei/releases/download/v3.3.5/nuclei_3.3.5_linux_amd64.zip", sha256 = "..." }

[playwright_chromium]
# Version pinned via the playwright npm package in desktop/package.json;
# fetcher invokes `npx playwright install chromium` rather than a URL.
managed_by = "playwright"
```

Pinned exact versions; updating means PR-ing the file. Initial SHAs will be populated during Task 1 of the implementation plan (run the fetcher once locally, capture the SHAs from the download).

### `scripts/fetch-tools.sh <platform>`

Bash script, idempotent, fails loudly on checksum mismatch:

1. Parse `tool-versions.toml` (via `python3 -c "import tomllib; ..."` since bash can't parse TOML natively, but the project already has Python available in CI).
2. For each tool except Playwright Chromium:
   - `curl -L <url> -o <tmp>`.
   - `sha256sum <tmp>` → compare against pinned value. Fail if mismatch.
   - Extract archive into `desktop/resources/tools/<platform>/`.
   - macOS nmap is a `.dmg` — mount via `hdiutil`, copy the binary, eject.
   - Strip archive structure: end state is a single executable at `desktop/resources/tools/<platform>/<toolname>`.
   - `chmod +x` on Unix.
3. For Playwright Chromium:
   - `PLAYWRIGHT_BROWSERS_PATH=$(pwd)/desktop/resources/tools/<platform>/playwright npx --prefix desktop playwright install chromium`.
4. Generate `desktop/resources/tools/<platform>/LICENSES.md` from the tool versions plus a static license header per tool.

### Cross-platform binary handling

Filenames inside `resources/tools/<platform>/` are bare (`nmap`, `ffuf`, etc. — no `.exe` suffix). The future Windows job will add `.exe` because Windows looks it up automatically when present on PATH.

### License & redistribution

All five tools are open-source under permissive licenses (Nmap Public Source License, MIT for `ffuf`/`gobuster`, MIT for `nuclei`, BSD for Chromium). Redistribution is allowed; the fetcher pulls from upstream official mirrors without repackaging. `LICENSES.md` records each tool's version, upstream URL, and license name.

## 5. Electron supervisor changes

Three focused edits to `desktop/electron/python.ts`:

### `isPackaged()` helper

```ts
function isPackaged(): boolean {
  return app.isPackaged;
}
```

### `resolveSpawnCommand(projectRoot)`

Returns `{ cmd, args, cwd }` or `{ error }`:

```ts
function resolveSpawnCommand(projectRoot: string):
  { cmd: string; args: string[]; cwd: string } | { error: string } {
  if (app.isPackaged) {
    const exe = process.platform === "win32" ? "reverser-service.exe" : "reverser-service";
    const cmd = path.join(process.resourcesPath, "python-dist", "reverser-service", exe);
    return {
      cmd,
      args: ["--host", "127.0.0.1", "--port", "0", "--project-root", projectRoot],
      cwd: projectRoot,
    };
  }
  const probe = findPython(projectRoot);
  if (!probe || !probe.cmd) {
    return { error: probe?.reason ?? "no Python found; run from inside `devenv shell`" };
  }
  return {
    cmd: probe.cmd,
    args: ["-u", "-m", "reverser.gui_service",
           "--host", "127.0.0.1", "--port", "0", "--project-root", projectRoot],
    cwd: projectRoot,
  };
}
```

### `buildPythonEnv(projectRoot)` extension

Existing implementation prepends `<root>/src` to `PYTHONPATH`. In packaged mode:

- Drop the `PYTHONPATH` prepend (PyInstaller embeds everything).
- Append `<resourcesPath>/tools/<platform>/` to `PATH`.
- Set `PLAYWRIGHT_BROWSERS_PATH=<resourcesPath>/tools/<platform>/playwright`.

In dev mode the function keeps its current behavior unchanged. The branch is a single `if (app.isPackaged)` block.

### `defaultProjectRoot()` extension

Returns the dev-mode parent-of-app-path today. Extended:

```ts
export function defaultProjectRoot(): string {
  if (app.isPackaged) {
    return path.join(app.getPath("userData"), "project");
  }
  return path.resolve(app.getAppPath(), "..");
}
```

`startSupervisor()` (in `main.ts`) calls `ensureProjectRootExists(defaultProjectRoot())` before spawn — a new function that creates `<projectRoot>/targets/` if it doesn't exist.

### `PythonSupervisor.start()` body

Reduced to: call `resolveSpawnCommand()`, on `{ error }` fire `onExit` immediately, otherwise call `spawn(cmd, args, { cwd, env: buildPythonEnv(projectRoot) })`. The rest (handshake parsing, stderr buffering, exit handling) is untouched.

## 6. Versioning, release flow & local builds

### Version source of truth

`pyproject.toml`'s `[project] version = "..."`. A small script `scripts/sync-version.mjs` reads it and overwrites `desktop/package.json`'s `version` field before `electron-builder` runs. Called from `build-desktop.sh`. SemVer; pre-releases get `-rc.N` suffix.

### Release flow (operator's perspective)

1. Bump `pyproject.toml` version.
2. `git tag v0.2.0 && git push origin v0.2.0`.
3. GitHub Actions runs the matrix + release job. Artifacts land on GitHub Releases ~15 min later, mirrored to gitea.
4. The metadata files (`latest-mac.yml`, `latest-linux.yml`) are published; existing installed apps would pick them up if/when `autoUpdater.checkForUpdates()` is enabled (v1.1).

### Shared scripts

```
scripts/
├── fetch-tools.sh <platform>       # downloads + verifies + extracts Tier-1 tools
├── build-python.sh                 # pyinstaller desktop/reverser-service.spec
└── build-desktop.sh [--installer]  # npm ci, npm run build, npx electron-builder
                                    # default = --dir (unpacked); --installer = full
```

Each script is small (10–30 lines), self-contained, exits non-zero on failure.

### `devenv.nix` additions

```nix
scripts.package.exec = ''
  set -euo pipefail
  ./scripts/fetch-tools.sh "$(uname -s)-$(uname -m)"
  ./scripts/build-python.sh
  ./scripts/build-desktop.sh
'';

scripts.package-installer.exec = ''
  set -euo pipefail
  ./scripts/fetch-tools.sh "$(uname -s)-$(uname -m)"
  ./scripts/build-python.sh
  ./scripts/build-desktop.sh --installer
'';
```

### CI calls the same scripts

```yaml
- run: ./scripts/fetch-tools.sh ${{ matrix.platform }}
- uses: actions/cache@v4   # PyInstaller intermediate build dir
- run: ./scripts/build-python.sh
- uses: actions/cache@v4   # node_modules
- run: ./scripts/build-desktop.sh --installer
```

Cache hooks live in CI yaml; the underlying commands are identical to devenv. No duplication of build logic.

## 7. Testing

### Existing tests run unchanged

All 750+ pytest tests and the current Playwright e2e suite test the source tree and continue to run on PRs (the matrix's PR-mode runs steps 1–6, which builds artifacts but doesn't test them as installed apps). A pytest run before `build-python.sh` catches Python-side regressions before they reach the installer.

### Packaged-app smoke test (CI)

After step 6 in each matrix job, run `desktop/tests/packaged/smoke.spec.ts` (new) — a small Playwright spec that points at the packaged binary:

```ts
const exe = process.platform === "darwin"
  ? "dist/mac-arm64/reverser.app/Contents/MacOS/reverser"
  : "dist/linux-unpacked/reverser";
const app = await electron.launch({ executablePath: exe, args: [] });
```

Two assertions:
- Dashboard renders with at least 10 profile cards within 60s (probes that the bundled Python service spawned and the WebSocket handshake completed).
- The existing `/api/health` endpoint reports `nmap` available (probes that the bundled nmap is on PATH).

This spec only runs in CI; local `package` / `package-installer` runs don't trigger it.

### Manual release checklist

`docs/release-checklist.md` (new, committed). Before tagging a release, the operator manually verifies on each platform:

- [ ] Installer opens (no notarization errors on macOS).
- [ ] App launches; Dashboard loads.
- [ ] Create a session; send a message; receive an agent response (full handshake + agent loop).
- [ ] `nmap` profile actually scans a localhost target.
- [ ] Playwright Chromium launches when the `webpentest` profile is used.

## 8. File layout (created / modified)

### Created

```
desktop/reverser-service.spec                    PyInstaller spec for the Python service
desktop/electron-builder.yml                     electron-builder config
desktop/tests/packaged/smoke.spec.ts             packaged-app smoke test
scripts/tool-versions.toml                       pinned tool versions + SHAs
scripts/fetch-tools.sh                           tool downloader
scripts/build-python.sh                          PyInstaller wrapper
scripts/build-desktop.sh                         npm + electron-builder wrapper
scripts/sync-version.mjs                         pyproject.toml → package.json version sync
.github/workflows/build.yml                      matrix CI workflow
.github/workflows/release.yml                    tag-triggered release job
docs/release-checklist.md                        manual release checklist
```

### Modified

```
desktop/package.json                             add electron-builder, electron-updater devDeps
desktop/electron/python.ts                       isPackaged(), resolveSpawnCommand(), env extensions
desktop/electron/main.ts                         ensureProjectRootExists() call
devenv.nix                                       scripts.package + scripts.package-installer
.gitignore                                       desktop/python-dist/, desktop/resources/tools/<platform>/, desktop/dist/
README.md                                        install instructions + xattr workaround for macOS
```

## 9. Out of scope (this phase)

- Project B — in-app tool manager, Tier-2 downloads (`hashcat`, etc.), missing-tool UX surfacing.
- Windows builds (CI hook reserved).
- macOS Intel (x64).
- Linux `.deb` / `.rpm`.
- Code signing certs purchased (env vars wired).
- In-app `autoUpdater.checkForUpdates()` call (metadata published).
- arm64 Linux.
- Tool version upgrades from inside the app.

## 10. Risks

| Risk | Mitigation |
|---|---|
| PyInstaller chokes on a Python dep (Pydantic v2, anyio, FastAPI all use C extensions and runtime introspection). | Build the spec file empirically: run PyInstaller, see what's missing, add `hiddenimports`/`datas` entries. Budget extra time in the first task. |
| Linux AppImage built against Ubuntu 26.04 glibc won't run on older distros. | Document the minimum glibc version in the README. Switching to `manylinux` containers for an older glibc baseline is a v1.1 option. |
| macOS Gatekeeper / quarantine blocks the unsigned `.dmg`. | README shows the `xattr -d com.apple.quarantine /Applications/reverser.app` workaround. Signing is wired to turn on later. |
| Upstream tool download mirror disappears or moves. | SHA-256 pinning makes the failure deterministic — the fetcher fails loudly. CI breakage is the signal; manual `tool-versions.toml` update is the recovery. |
| Bundled binary versions drift from what's documented. | `LICENSES.md` generated at build time records each tool's version and upstream URL. |
| Final installer size balloons past ~500MB. | Acceptable for a desktop pentest tool. Project B's Tier-2 fetcher addresses it by moving optional tools out of the bundle. |
| PyInstaller bundle picks up dev-only deps. | The `.spec` file uses `--exclude-module pytest` etc.; the spec is committed and reviewed. |
| `app.isPackaged` is true during `electron-builder --dir` development testing, breaking dev iteration. | `--dir` produces an unpacked bundle where `isPackaged` is also true, which is intentional — that's how we test packaged-mode behavior. For live source-tree dev, keep using `npm run dev`. |
| First-launch project-root migration. Users with existing source-tree `targets/` dirs lose access to that data when they install the packaged app. | Document in the README: existing users should `cp -r targets/ ~/Library/Application\ Support/reverser/project/` (macOS) or `~/.config/reverser/project/` (Linux) before first launch of the packaged build. |

## 11. Open questions

None blocking.
