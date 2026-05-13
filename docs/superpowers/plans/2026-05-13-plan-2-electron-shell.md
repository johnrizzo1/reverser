# Electron Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Electron + Vite + React desktop app shell. After this plan, launching `npm run dev` opens an Electron window that auto-spawns the Plan-1 Python service, authenticates with the handshake token, and renders the list of profiles + the health snapshot. No engagement features yet — those land in Plan 3.

**Architecture:** Electron main (Node) supervises a Python child via the Plan-1 handshake protocol, then loads a sandboxed renderer that talks to `http://127.0.0.1:<port>` with the bearer token. Renderer uses React + TS + Tailwind + shadcn/ui + TanStack Query. Plumbing only — the visible product is a "Dashboard" page that lists profiles and a "Health" page that lists service checks.

**Tech Stack:** Electron 32, Vite 5, `vite-plugin-electron`, React 18, TypeScript 5, Tailwind 3, shadcn/ui, TanStack Query 5, Zustand 4, react-router-dom 6, Playwright (e2e).

**Depends on:** [`Plan 1 — GUI Service Foundation`](2026-05-13-plan-1-gui-service-foundation.md). Plan 1 must be merged or coexist on the branch.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md`](../specs/2026-05-13-electron-desktop-ui-design.md) — sections 2 (architecture), 4 (renderer), 5 (lifecycle/security).

---

## File map

```
desktop/                                          create (directory)
desktop/package.json                              create
desktop/tsconfig.json                             create  (renderer config)
desktop/tsconfig.node.json                        create  (vite config TS)
desktop/tsconfig.electron.json                    create  (main/preload TS)
desktop/vite.config.ts                            create
desktop/tailwind.config.ts                        create
desktop/postcss.config.cjs                        create
desktop/index.html                                create
desktop/.gitignore                                create
desktop/electron/main.ts                          create  (Electron main entry)
desktop/electron/preload.ts                       create
desktop/electron/python.ts                        create  (supervisor)
desktop/electron/ipc.ts                           create  (channel constants)
desktop/renderer/src/main.tsx                     create
desktop/renderer/src/App.tsx                      create
desktop/renderer/src/index.css                    create
desktop/renderer/src/api/client.ts                create
desktop/renderer/src/api/queries.ts               create  (TanStack Query hooks)
desktop/renderer/src/state/connection.ts          create  (Zustand store for {port, token, status})
desktop/renderer/src/pages/Dashboard.tsx          create
desktop/renderer/src/pages/Health.tsx             create
desktop/renderer/src/pages/CrashScreen.tsx        create
desktop/renderer/src/layout/Shell.tsx             create
desktop/renderer/src/layout/StatusBar.tsx         create
desktop/renderer/src/layout/ActivityBar.tsx       create
desktop/renderer/src/layout/Footer.tsx            create
desktop/renderer/src/components/ui/button.tsx     create  (shadcn)
desktop/renderer/src/components/ui/card.tsx       create  (shadcn)
desktop/renderer/src/lib/utils.ts                 create  (cn helper)
desktop/tests/e2e/smoke.spec.ts                   create
desktop/playwright.config.ts                      create
.gitignore                                        modify  (add desktop/node_modules + desktop/dist)
```

---

## Task 1: `desktop/` scaffold + `package.json` + deps

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/.gitignore`
- Modify: `.gitignore` (root)

- [ ] **Step 1: Create the `desktop/` directory**

```bash
mkdir -p /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.claude/worktrees/elegant-matsumoto-69c737/desktop
```

- [ ] **Step 2: Write `desktop/package.json`**

Create with this exact content:

```json
{
  "name": "reverser-desktop",
  "private": true,
  "version": "0.1.0",
  "description": "Electron desktop UI for reverser",
  "main": "dist-electron/main.js",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -p tsconfig.electron.json && tsc -b && vite build",
    "preview": "vite preview",
    "lint": "tsc -b --noEmit",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "lucide-react": "^0.456.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.27.0",
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
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.14",
    "typescript": "^5.6.3",
    "vite": "^5.4.10",
    "vite-plugin-electron": "^0.28.8",
    "vite-plugin-electron-renderer": "^0.14.6"
  }
}
```

- [ ] **Step 3: Write `desktop/.gitignore`**

```gitignore
node_modules/
dist/
dist-electron/
.vite/
test-results/
playwright-report/
*.log
```

- [ ] **Step 4: Update root `.gitignore`**

Open the existing root `.gitignore` and append a section to skip the desktop build outputs (the per-folder ignore in step 3 covers node_modules already, but a top-level entry makes find/grep cleaner):

```gitignore
# Electron desktop UI build outputs
desktop/node_modules/
desktop/dist/
desktop/dist-electron/
desktop/.vite/
desktop/test-results/
desktop/playwright-report/
```

- [ ] **Step 5: Install dependencies**

```bash
cd desktop && npm install
```

Expected: `node_modules/` populated, no errors. Electron downloads its binary (~80 MB on first install).

- [ ] **Step 6: Commit**

```bash
git add desktop/package.json desktop/.gitignore .gitignore
git commit -m "feat(desktop): scaffold + package.json + npm deps

Establishes the Electron + Vite + React workspace at desktop/ with the
full dep set planned for v1 (TanStack Query, Zustand, react-router,
Tailwind, shadcn/ui primitives, Playwright for e2e). Build outputs are
gitignored; the lockfile (package-lock.json) is committed."
```

Note: also commit `desktop/package-lock.json` if `npm install` created one (it will).

```bash
git add desktop/package-lock.json
git commit --amend --no-edit
```

(Amend is OK here because the commit was just created in the previous step and not pushed.)

---

## Task 2: TypeScript + Vite + Tailwind config

**Files:**
- Create: `desktop/tsconfig.json`
- Create: `desktop/tsconfig.node.json`
- Create: `desktop/tsconfig.electron.json`
- Create: `desktop/vite.config.ts`
- Create: `desktop/tailwind.config.ts`
- Create: `desktop/postcss.config.cjs`
- Create: `desktop/index.html`
- Create: `desktop/renderer/src/index.css`
- Create: `desktop/renderer/src/main.tsx`
- Create: `desktop/renderer/src/App.tsx`

- [ ] **Step 1: `tsconfig.json` (renderer config)**

Create `desktop/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["renderer/src/*"]
    }
  },
  "include": ["renderer/src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 2: `tsconfig.node.json` (vite config TS)**

Create `desktop/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 3: `tsconfig.electron.json` (main + preload, emits to dist-electron)**

Create `desktop/tsconfig.electron.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "moduleResolution": "Node",
    "outDir": "dist-electron",
    "rootDir": "electron",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": false,
    "types": ["node"]
  },
  "include": ["electron/**/*"]
}
```

(Electron main + preload use CommonJS for maximal compatibility with Electron's loader; the renderer uses ESM.)

- [ ] **Step 4: `vite.config.ts`**

Create `desktop/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import electron from "vite-plugin-electron";
import renderer from "vite-plugin-electron-renderer";
import { resolve } from "path";

export default defineConfig({
  root: "renderer",
  publicDir: false,
  resolve: {
    alias: {
      "@": resolve(__dirname, "renderer/src"),
    },
  },
  plugins: [
    react(),
    electron([
      {
        entry: "electron/main.ts",
        vite: {
          build: {
            outDir: "dist-electron",
            rollupOptions: {
              external: ["electron"],
            },
          },
        },
      },
      {
        entry: "electron/preload.ts",
        onstart(args) {
          args.reload();
        },
        vite: {
          build: {
            outDir: "dist-electron",
            rollupOptions: {
              external: ["electron"],
            },
          },
        },
      },
    ]),
    renderer(),
  ],
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
```

- [ ] **Step 5: `tailwind.config.ts`**

Create `desktop/tailwind.config.ts`:

```ts
import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["renderer/index.html", "renderer/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Severity (from spec section 4)
        severity: {
          high: "#ef4444",
          medium: "#f59e0b",
          low: "#3b82f6",
          info: "#6b7280",
        },
        // Hypothesis status
        status: {
          confirmed: "#22c55e",
          testing: "#f59e0b",
          proposed: "#6b7280",
          refuted: "#ef4444",
          abandoned: "#4b5563",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 6: `postcss.config.cjs`**

Create `desktop/postcss.config.cjs`:

```js
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 7: `renderer/index.html` (Vite-root entry)**

Vite is configured with `root: "renderer"` (Step 4), so `index.html` lives inside `renderer/` and its script src is relative to that root.

```bash
mkdir -p desktop/renderer
```

Create `desktop/renderer/index.html`:

```html
<!doctype html>
<html lang="en" class="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta
      http-equiv="Content-Security-Policy"
      content="default-src 'self'; connect-src http://127.0.0.1:* ws://127.0.0.1:*; img-src 'self' http://127.0.0.1:* data:; style-src 'self' 'unsafe-inline'; script-src 'self'"
    />
    <title>reverser</title>
  </head>
  <body class="bg-neutral-950 text-neutral-100 h-screen w-screen overflow-hidden">
    <div id="root" class="h-full w-full"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: `renderer/src/index.css`**

Create `desktop/renderer/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    color-scheme: dark;
  }
  body {
    -webkit-font-smoothing: antialiased;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
      sans-serif;
  }
}
```

- [ ] **Step 9: `renderer/src/main.tsx`**

Create `desktop/renderer/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
```

- [ ] **Step 10: `renderer/src/App.tsx` (placeholder)**

Create `desktop/renderer/src/App.tsx`:

```tsx
export default function App() {
  return (
    <div className="h-full w-full flex items-center justify-center">
      <p className="text-neutral-400">reverser desktop — scaffolding…</p>
    </div>
  );
}
```

- [ ] **Step 11: Verify the renderer builds**

```bash
cd desktop && npx tsc -b
```

Expected: no errors. (The electron main/preload aren't created yet — only the renderer + vite config are type-checked.)

- [ ] **Step 12: Commit**

```bash
git add desktop/tsconfig*.json desktop/vite.config.ts desktop/tailwind.config.ts \
        desktop/postcss.config.cjs desktop/renderer/
git commit -m "feat(desktop): TypeScript + Vite + Tailwind + React entry"
```

---

## Task 3: Electron main process (window lifecycle, no Python yet)

**Files:**
- Create: `desktop/electron/main.ts`
- Create: `desktop/electron/preload.ts`
- Create: `desktop/electron/ipc.ts`

- [ ] **Step 1: `ipc.ts` (channel constants)**

Create `desktop/electron/ipc.ts`:

```ts
/**
 * IPC channel names shared between main and preload.
 * Keeping them in one file prevents typo-driven channel mismatches.
 */
export const IPC = {
  // Renderer → Main (invoke/reply)
  GET_CONNECTION_INFO: "connection:get-info",
  OPEN_EXTERNAL: "shell:open-external",
  OPEN_FILE_DIALOG: "dialog:open-file",

  // Main → Renderer (send)
  CONNECTION_STATUS_CHANGED: "connection:status-changed",
  PYTHON_LOG_LINE: "python:log-line",
} as const;
```

- [ ] **Step 2: `preload.ts`**

Create `desktop/electron/preload.ts`:

```ts
import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "./ipc";

export type ConnectionInfo = {
  status: "starting" | "ready" | "error" | "exited";
  port: number | null;
  token: string | null;
  errorMessage: string | null;
};

contextBridge.exposeInMainWorld("desktop", {
  getConnectionInfo: (): Promise<ConnectionInfo> =>
    ipcRenderer.invoke(IPC.GET_CONNECTION_INFO),
  onConnectionStatusChanged: (cb: (info: ConnectionInfo) => void) => {
    const listener = (_: unknown, info: ConnectionInfo) => cb(info);
    ipcRenderer.on(IPC.CONNECTION_STATUS_CHANGED, listener);
    return () => ipcRenderer.removeListener(IPC.CONNECTION_STATUS_CHANGED, listener);
  },
  onPythonLogLine: (cb: (line: string) => void) => {
    const listener = (_: unknown, line: string) => cb(line);
    ipcRenderer.on(IPC.PYTHON_LOG_LINE, listener);
    return () => ipcRenderer.removeListener(IPC.PYTHON_LOG_LINE, listener);
  },
  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke(IPC.OPEN_EXTERNAL, url),
  openFileDialog: (): Promise<string | null> =>
    ipcRenderer.invoke(IPC.OPEN_FILE_DIALOG),
});

declare global {
  interface Window {
    desktop: {
      getConnectionInfo: () => Promise<ConnectionInfo>;
      onConnectionStatusChanged: (cb: (info: ConnectionInfo) => void) => () => void;
      onPythonLogLine: (cb: (line: string) => void) => () => void;
      openExternal: (url: string) => Promise<void>;
      openFileDialog: () => Promise<string | null>;
    };
  }
}
```

- [ ] **Step 3: `main.ts` (window only — Python supervisor added in Task 4)**

Create `desktop/electron/main.ts`:

```ts
import { app, BrowserWindow, ipcMain, shell, dialog } from "electron";
import path from "path";
import { IPC } from "./ipc";

let mainWindow: BrowserWindow | null = null;

// Connection state is owned by main; renderer reads via IPC.
// Initial state — the supervisor (Task 4) will fill in port/token.
let connectionInfo: {
  status: "starting" | "ready" | "error" | "exited";
  port: number | null;
  token: string | null;
  errorMessage: string | null;
} = {
  status: "starting",
  port: null,
  token: null,
  errorMessage: null,
};

function broadcastConnectionInfo() {
  mainWindow?.webContents.send(IPC.CONNECTION_STATUS_CHANGED, connectionInfo);
}

export function setConnectionInfo(
  next: Partial<typeof connectionInfo>
): void {
  connectionInfo = { ...connectionInfo, ...next };
  broadcastConnectionInfo();
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 600,
    backgroundColor: "#0a0a0a",
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

// IPC handlers
ipcMain.handle(IPC.GET_CONNECTION_INFO, () => connectionInfo);

ipcMain.handle(IPC.OPEN_EXTERNAL, async (_event, url: string) => {
  // Only allow http(s) and file:// — never arbitrary protocol handlers
  if (!/^(https?:|file:)/.test(url)) {
    throw new Error("refusing to open non-http/file URL");
  }
  await shell.openExternal(url);
});

ipcMain.handle(IPC.OPEN_FILE_DIALOG, async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

app.whenReady().then(async () => {
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
```

- [ ] **Step 4: Compile electron sources**

```bash
cd desktop && npx tsc -p tsconfig.electron.json
```

Expected: no errors. Outputs `dist-electron/main.js`, `dist-electron/preload.js`, `dist-electron/ipc.js`.

- [ ] **Step 5: Launch dev and verify a blank window opens**

```bash
cd desktop && npm run dev
```

Expected:
- Vite starts on `http://localhost:5173`.
- An Electron window opens within ~3 s, dev tools attached.
- The window shows "reverser desktop — scaffolding…" text.
- Closing the window quits the app.

Quit with Ctrl-C in the terminal or by closing the window.

- [ ] **Step 6: Commit**

```bash
git add desktop/electron/
git commit -m "feat(desktop): Electron main + preload + IPC scaffold

Sandboxed renderer (nodeIntegration:false, contextIsolation:true,
sandbox:true). contextBridge exposes only get-connection-info, open-
external (http/file only), open-file dialog, and event subscriptions.
Strict CSP in index.html restricts connect-src to 127.0.0.1."
```

---

## Task 4: Python supervisor — spawn, handshake, kill on quit

**Files:**
- Create: `desktop/electron/python.ts`
- Modify: `desktop/electron/main.ts`

The supervisor spawns `python -m reverser.gui_service`, reads the handshake line from stdout, fills in `connectionInfo`, and tears down on app quit. Forwards subsequent stdout/stderr as log lines to the renderer.

- [ ] **Step 1: Create `python.ts`**

Create `desktop/electron/python.ts`:

```ts
import { spawn, ChildProcess } from "child_process";
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

export class PythonSupervisor {
  private proc: ChildProcess | null = null;
  private exited = false;

  constructor(private opts: SupervisorOptions) {}

  start(): void {
    if (this.proc) throw new Error("already started");

    // `python` from the user's devenv shell PATH. The Electron app is
    // expected to be launched from inside `devenv shell` (per the spec —
    // we do not bundle the Python interpreter).
    const proc = spawn(
      "python",
      ["-u", "-m", "reverser.gui_service",
       "--host", "127.0.0.1", "--port", "0",
       "--project-root", this.opts.projectRoot],
      {
        cwd: this.opts.projectRoot,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      }
    );

    this.proc = proc;

    const stdoutRl = readline.createInterface({ input: proc.stdout! });
    let handshakeSeen = false;

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
    stderrRl.on("line", (line) => this.opts.onLogLine(line));

    proc.on("exit", (code, signal) => {
      if (this.exited) return;
      this.exited = true;
      const reason = handshakeSeen
        ? `service exited (code=${code}, signal=${signal})`
        : `service died before handshake (code=${code}, signal=${signal})`;
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

/** Resolve the project root. In dev, that's the parent of the desktop/ dir. */
export function defaultProjectRoot(): string {
  // dist-electron/main.js runs from desktop/dist-electron/, so two levels up
  // is the project root.
  return path.resolve(app.getAppPath(), "..");
}
```

- [ ] **Step 2: Wire the supervisor into `main.ts`**

Edit `desktop/electron/main.ts`. Replace its current body with the following (preserving the imports already there, plus the supervisor wiring):

```ts
import { app, BrowserWindow, ipcMain, shell, dialog } from "electron";
import path from "path";
import { IPC } from "./ipc";
import { PythonSupervisor, defaultProjectRoot } from "./python";

let mainWindow: BrowserWindow | null = null;
let supervisor: PythonSupervisor | null = null;

let connectionInfo: {
  status: "starting" | "ready" | "error" | "exited";
  port: number | null;
  token: string | null;
  errorMessage: string | null;
} = {
  status: "starting",
  port: null,
  token: null,
  errorMessage: null,
};

function broadcastConnectionInfo() {
  mainWindow?.webContents.send(IPC.CONNECTION_STATUS_CHANGED, connectionInfo);
}

function setConnectionInfo(next: Partial<typeof connectionInfo>) {
  connectionInfo = { ...connectionInfo, ...next };
  broadcastConnectionInfo();
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900,
    minWidth: 1000, minHeight: 600,
    backgroundColor: "#0a0a0a",
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => { mainWindow = null; });

  if (process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function startSupervisor() {
  supervisor = new PythonSupervisor({
    projectRoot: defaultProjectRoot(),
    onReady: (h) => {
      setConnectionInfo({
        status: "ready", port: h.port, token: h.token, errorMessage: null,
      });
    },
    onExit: ({ reason }) => {
      setConnectionInfo({
        status: "exited", errorMessage: reason,
      });
    },
    onLogLine: (line) => {
      mainWindow?.webContents.send(IPC.PYTHON_LOG_LINE, line);
    },
  });
  supervisor.start();
}

ipcMain.handle(IPC.GET_CONNECTION_INFO, () => connectionInfo);
ipcMain.handle(IPC.OPEN_EXTERNAL, async (_e, url: string) => {
  if (!/^(https?:|file:)/.test(url)) throw new Error("refusing non-http/file URL");
  await shell.openExternal(url);
});
ipcMain.handle(IPC.OPEN_FILE_DIALOG, async () => {
  if (!mainWindow) return null;
  const r = await dialog.showOpenDialog(mainWindow, { properties: ["openFile"] });
  return r.canceled ? null : r.filePaths[0] ?? null;
});

app.whenReady().then(async () => {
  startSupervisor();
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", async (e) => {
  if (supervisor) {
    e.preventDefault();
    await supervisor.stop();
    supervisor = null;
    app.quit();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
```

- [ ] **Step 3: Update the renderer to display connection status**

Replace `desktop/renderer/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { ConnectionInfo } from "../../electron/preload";

export default function App() {
  const [info, setInfo] = useState<ConnectionInfo | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  useEffect(() => {
    window.desktop.getConnectionInfo().then(setInfo);
    const off1 = window.desktop.onConnectionStatusChanged(setInfo);
    const off2 = window.desktop.onPythonLogLine((line) =>
      setLogs((prev) => [...prev.slice(-199), line])
    );
    return () => { off1(); off2(); };
  }, []);

  return (
    <div className="h-full w-full p-6 flex flex-col gap-4">
      <h1 className="text-lg font-medium">reverser desktop</h1>
      <div className="rounded border border-neutral-800 p-4 text-sm">
        <div>status: <span className="font-mono">{info?.status ?? "?"}</span></div>
        <div>port: <span className="font-mono">{info?.port ?? "—"}</span></div>
        <div>token: <span className="font-mono">{info?.token ? `${info.token.slice(0, 8)}…` : "—"}</span></div>
        {info?.errorMessage && (
          <div className="text-red-400 mt-2">{info.errorMessage}</div>
        )}
      </div>
      <div className="flex-1 rounded border border-neutral-800 p-3 overflow-auto text-xs font-mono text-neutral-400">
        {logs.length === 0 ? <em>no python logs yet</em> : logs.map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </div>
    </div>
  );
}
```

(The `import type ... ConnectionInfo` path uses the preload's type. We'll narrow this in Task 5 with a proper type export.)

- [ ] **Step 4: Manual smoke**

Make sure you are in `devenv shell` so `python -m reverser.gui_service` is on PATH.

```bash
cd desktop && npm run dev
```

Expected:
- Electron window opens.
- Status flips from `starting` to `ready` within ~2 s.
- `port` shows a number, `token` shows the first 8 chars + "…".
- Logs panel shows uvicorn startup lines.
- Closing the window: Python child receives SIGTERM and exits (visible in the terminal that ran `npm run dev` — no leftover process).

- [ ] **Step 5: Commit**

```bash
git add desktop/electron/ desktop/renderer/src/App.tsx
git commit -m "feat(desktop): Python supervisor — spawn, handshake, kill on quit

Electron main spawns 'python -m reverser.gui_service', reads the handshake
JSON line, exposes the connection info to the renderer via contextBridge,
and forwards subsequent stdout/stderr as log lines. SIGTERM on app quit
with a 5s SIGKILL fallback."
```

---

## Task 5: API client (renderer ↔ service via bearer token)

**Files:**
- Create: `desktop/renderer/src/lib/utils.ts`
- Create: `desktop/renderer/src/state/connection.ts`
- Create: `desktop/renderer/src/api/client.ts`
- Create: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: `lib/utils.ts` — the standard `cn` helper**

Create `desktop/renderer/src/lib/utils.ts`:

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 2: `state/connection.ts` — Zustand store for {port, token, status}**

Create `desktop/renderer/src/state/connection.ts`:

```ts
import { create } from "zustand";
import type { ConnectionInfo } from "../../../electron/preload";

type ConnectionState = ConnectionInfo & {
  setInfo: (info: ConnectionInfo) => void;
};

export const useConnection = create<ConnectionState>((set) => ({
  status: "starting",
  port: null,
  token: null,
  errorMessage: null,
  setInfo: (info) => set(info),
}));

/** Mount-once hook: subscribe to main-process connection updates. */
export function useConnectionSubscription() {
  // Note: caller is responsible for calling this exactly once at app root.
  // Subsequent components read state via useConnection() selectors.
}
```

- [ ] **Step 3: `api/client.ts` — fetch wrapper with bearer token + base URL**

Create `desktop/renderer/src/api/client.ts`:

```ts
import { useConnection } from "@/state/connection";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
  }
}

function buildHeaders(token: string, extra?: HeadersInit): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...extra };
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown
): Promise<T> {
  const { port, token, status } = useConnection.getState();
  if (status !== "ready" || !port || !token) {
    throw new ApiError(0, null, "service not ready");
  }
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method,
    headers: buildHeaders(token),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let parsed: unknown = null;
    try { parsed = await res.json(); } catch { /* ignore */ }
    throw new ApiError(res.status, parsed, `${method} ${path} → ${res.status}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export type HealthResponse = {
  ok: boolean;
  version: string;
  checks: Record<string, { ok: boolean; detail: string | null }>;
};

export type Profile = {
  key: string;
  name: string;
  description: string;
  skills: { name: string; key: string; description: string }[];
  tools_allowlist: string[] | null;
};

export type ProfilesResponse = { profiles: Profile[] };

export type Backend = {
  key: string;
  name: string;
  default_api_base: string | null;
  requires_api_key: boolean;
  requires_model: boolean;
};

export type BackendsResponse = { backends: Backend[] };
```

- [ ] **Step 4: `api/queries.ts` — TanStack Query hooks**

Create `desktop/renderer/src/api/queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api, type HealthResponse, type ProfilesResponse, type BackendsResponse } from "./client";
import { useConnection } from "@/state/connection";

function useReady() {
  return useConnection((s) => s.status === "ready");
}

export function useHealth() {
  const ready = useReady();
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/api/health"),
    enabled: ready,
    refetchInterval: 10_000,
  });
}

export function useProfiles() {
  const ready = useReady();
  return useQuery({
    queryKey: ["profiles"],
    queryFn: () => api.get<ProfilesResponse>("/api/profiles"),
    enabled: ready,
    staleTime: 60_000,
  });
}

export function useBackends() {
  const ready = useReady();
  return useQuery({
    queryKey: ["backends"],
    queryFn: () => api.get<BackendsResponse>("/api/backends"),
    enabled: ready,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 5: Compile-check**

```bash
cd desktop && npx tsc -b
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/lib/ desktop/renderer/src/state/ desktop/renderer/src/api/
git commit -m "feat(desktop): API client + TanStack Query hooks for health/profiles/backends"
```

---

## Task 6: Layout shell — StatusBar, ActivityBar, Footer

The IDE-style layout per spec section 4: status bar across top, activity bar on left (48 px), side panel, center, footer.

**Files:**
- Create: `desktop/renderer/src/components/ui/button.tsx`
- Create: `desktop/renderer/src/components/ui/card.tsx`
- Create: `desktop/renderer/src/layout/Shell.tsx`
- Create: `desktop/renderer/src/layout/StatusBar.tsx`
- Create: `desktop/renderer/src/layout/ActivityBar.tsx`
- Create: `desktop/renderer/src/layout/Footer.tsx`

- [ ] **Step 1: Minimal shadcn-style Button**

Create `desktop/renderer/src/components/ui/button.tsx`:

```tsx
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded text-sm font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        default: "bg-neutral-100 text-neutral-900 hover:bg-neutral-200",
        ghost: "hover:bg-neutral-800 text-neutral-200",
        outline: "border border-neutral-700 hover:bg-neutral-800 text-neutral-200",
        destructive: "bg-red-600 text-white hover:bg-red-500",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-7 px-2 text-xs",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  )
);
Button.displayName = "Button";
```

- [ ] **Step 2: Minimal shadcn-style Card**

Create `desktop/renderer/src/components/ui/card.tsx`:

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div ref={ref} className={cn("rounded border border-neutral-800 bg-neutral-900/40", className)} {...p} />
  )
);
Card.displayName = "Card";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div ref={ref} className={cn("p-4 border-b border-neutral-800", className)} {...p} />
  )
);
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...p }, ref) => (
    <h3 ref={ref} className={cn("text-sm font-medium text-neutral-100", className)} {...p} />
  )
);
CardTitle.displayName = "CardTitle";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...p }, ref) => (
    <div ref={ref} className={cn("p-4 text-sm text-neutral-300", className)} {...p} />
  )
);
CardContent.displayName = "CardContent";
```

- [ ] **Step 3: `StatusBar.tsx`**

Create `desktop/renderer/src/layout/StatusBar.tsx`:

```tsx
import { useConnection } from "@/state/connection";

export function StatusBar() {
  const status = useConnection((s) => s.status);
  const port = useConnection((s) => s.port);

  return (
    <header className="h-9 border-b border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-xs text-neutral-400 gap-4 font-mono">
      <span className="text-neutral-200">reverser</span>
      <span>·</span>
      <span>
        backend:{" "}
        <span className={status === "ready" ? "text-green-400" : status === "exited" ? "text-red-400" : "text-amber-400"}>
          {status}
        </span>
      </span>
      <span>·</span>
      <span>:{port ?? "—"}</span>
      <span className="ml-auto text-neutral-500">no active engagement</span>
    </header>
  );
}
```

- [ ] **Step 4: `ActivityBar.tsx`**

Create `desktop/renderer/src/layout/ActivityBar.tsx`:

```tsx
import { NavLink } from "react-router-dom";
import { Home, Heart, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS = [
  { to: "/", label: "Dashboard", icon: Home },
  { to: "/health", label: "Health", icon: Heart },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function ActivityBar() {
  return (
    <nav className="w-12 border-r border-neutral-800 bg-neutral-950 flex flex-col items-center py-2 gap-1">
      {ICONS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          title={label}
          className={({ isActive }) =>
            cn(
              "w-9 h-9 flex items-center justify-center rounded transition-colors",
              isActive ? "bg-neutral-800 text-neutral-100" : "text-neutral-500 hover:text-neutral-200 hover:bg-neutral-900"
            )
          }
        >
          <Icon className="w-4 h-4" />
        </NavLink>
      ))}
    </nav>
  );
}
```

- [ ] **Step 5: `Footer.tsx`**

Create `desktop/renderer/src/layout/Footer.tsx`:

```tsx
export function Footer() {
  return (
    <footer className="h-5 border-t border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-[10px] text-neutral-500 font-mono gap-3">
      <span>F1 skills</span>
      <span>F2 profile</span>
      <span>F4 sudo</span>
      <span>F6 stop</span>
      <span className="ml-auto">v0.1.0</span>
    </footer>
  );
}
```

- [ ] **Step 6: `Shell.tsx` (composes the layout)**

Create `desktop/renderer/src/layout/Shell.tsx`:

```tsx
import { Outlet } from "react-router-dom";
import { StatusBar } from "./StatusBar";
import { ActivityBar } from "./ActivityBar";
import { Footer } from "./Footer";

export function Shell() {
  return (
    <div className="h-full w-full flex flex-col bg-neutral-950 text-neutral-100">
      <StatusBar />
      <div className="flex-1 flex min-h-0">
        <ActivityBar />
        <main className="flex-1 min-w-0 overflow-auto">
          <Outlet />
        </main>
      </div>
      <Footer />
    </div>
  );
}
```

- [ ] **Step 7: Compile-check**

```bash
cd desktop && npx tsc -b
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add desktop/renderer/src/components/ desktop/renderer/src/layout/
git commit -m "feat(desktop): IDE-style layout shell — status bar, activity bar, footer"
```

---

## Task 7: Dashboard page (lists profiles) + Health page

**Files:**
- Create: `desktop/renderer/src/pages/Dashboard.tsx`
- Create: `desktop/renderer/src/pages/Health.tsx`
- Create: `desktop/renderer/src/pages/CrashScreen.tsx`
- Create: `desktop/renderer/src/pages/Settings.tsx` (placeholder)
- Modify: `desktop/renderer/src/App.tsx` (router wires Shell + pages)

- [ ] **Step 1: `Dashboard.tsx` — lists profiles**

Create `desktop/renderer/src/pages/Dashboard.tsx`:

```tsx
import { useProfiles } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function Dashboard() {
  const { data, isLoading, error } = useProfiles();

  return (
    <div className="p-6 max-w-5xl">
      <h2 className="text-base font-medium mb-1">Profiles</h2>
      <p className="text-xs text-neutral-500 mb-4">
        Engagement profiles registered in the backend. Plan-3 will turn each
        into a "New engagement" entry point.
      </p>

      {isLoading && <p className="text-sm text-neutral-500">loading…</p>}
      {error && (
        <p className="text-sm text-red-400">
          failed to load profiles: {String((error as Error).message)}
        </p>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {data?.profiles.map((p) => (
          <Card key={p.key}>
            <CardHeader>
              <CardTitle>{p.name}</CardTitle>
              <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">
                {p.key}
              </div>
            </CardHeader>
            <CardContent className="text-xs">
              <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
              {p.skills.length > 0 && (
                <p className="text-neutral-500 mt-2">
                  {p.skills.length} skill{p.skills.length === 1 ? "" : "s"}
                </p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `Health.tsx` — service health snapshot**

Create `desktop/renderer/src/pages/Health.tsx`:

```tsx
import { useHealth } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Check, X } from "lucide-react";

export function Health() {
  const { data, isLoading, error } = useHealth();

  return (
    <div className="p-6 max-w-3xl">
      <h2 className="text-base font-medium mb-1">Backend health</h2>
      <p className="text-xs text-neutral-500 mb-4">
        These checks run every 10 s. None of them block service startup —
        but a red entry usually means a feature won't work.
      </p>

      {isLoading && <p className="text-sm text-neutral-500">loading…</p>}
      {error && <p className="text-sm text-red-400">{String((error as Error).message)}</p>}

      <Card>
        <CardHeader>
          <CardTitle>checks · service v{data?.version ?? "?"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 font-mono text-xs">
          {data &&
            Object.entries(data.checks).map(([k, v]) => (
              <div key={k} className="flex items-start gap-3">
                {v.ok ? (
                  <Check className="w-4 h-4 mt-0.5 text-green-400 shrink-0" />
                ) : (
                  <X className="w-4 h-4 mt-0.5 text-red-400 shrink-0" />
                )}
                <div className="min-w-0">
                  <div className="text-neutral-200">{k}</div>
                  <div className="text-neutral-500 truncate">{v.detail ?? "—"}</div>
                </div>
              </div>
            ))}
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: `CrashScreen.tsx` — shown when Python child has exited**

Create `desktop/renderer/src/pages/CrashScreen.tsx`:

```tsx
import { Button } from "@/components/ui/button";
import { useConnection } from "@/state/connection";

export function CrashScreen() {
  const error = useConnection((s) => s.errorMessage);
  return (
    <div className="h-full flex items-center justify-center">
      <div className="max-w-lg text-center p-8 border border-red-900/60 rounded bg-red-950/30">
        <h2 className="text-base font-medium text-red-200 mb-2">Backend service exited</h2>
        <p className="text-xs text-neutral-400 mb-4">
          The Python service stopped unexpectedly. You can restart the app
          to bring it back. Phase 4 will add in-app restart.
        </p>
        {error && (
          <pre className="text-[10px] text-red-300 bg-black/40 rounded p-2 overflow-auto text-left mb-4">
            {error}
          </pre>
        )}
        <Button
          variant="outline"
          onClick={() => window.location.reload()}
        >
          Reload window
        </Button>
      </div>
    </div>
  );
}
```

(Reloading the window doesn't restart Python in v1 — but if Python comes back via a manual app restart, the renderer reconnects automatically. Full in-app restart is a Phase 4 enhancement.)

- [ ] **Step 4: `Settings.tsx` (placeholder)**

Create `desktop/renderer/src/pages/Settings.tsx`:

```tsx
export function Settings() {
  return (
    <div className="p-6">
      <h2 className="text-base font-medium mb-2">Settings</h2>
      <p className="text-xs text-neutral-500">Phase 4. API keys, backend selection, health, target hygiene.</p>
    </div>
  );
}
```

- [ ] **Step 5: Replace `App.tsx` with the router + connection subscription**

Replace `desktop/renderer/src/App.tsx`:

```tsx
import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "@/layout/Shell";
import { Dashboard } from "@/pages/Dashboard";
import { Health } from "@/pages/Health";
import { Settings } from "@/pages/Settings";
import { CrashScreen } from "@/pages/CrashScreen";
import { useConnection } from "@/state/connection";

export default function App() {
  const status = useConnection((s) => s.status);
  const setInfo = useConnection((s) => s.setInfo);

  useEffect(() => {
    window.desktop.getConnectionInfo().then(setInfo);
    return window.desktop.onConnectionStatusChanged(setInfo);
  }, [setInfo]);

  if (status === "exited") return <CrashScreen />;
  if (status === "starting") {
    return (
      <div className="h-full flex items-center justify-center text-sm text-neutral-500">
        starting backend…
      </div>
    );
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 6: Manual smoke**

```bash
cd desktop && npm run dev
```

Expected (in `devenv shell`):
- Window opens, briefly shows "starting backend…", then renders the Dashboard.
- The Profiles grid populates with 15 cards (general, linux, windows, …).
- Clicking the heart icon in the activity bar navigates to /health and shows the env checks.
- Clicking the gear icon goes to /settings (placeholder).
- Quitting the app shuts the Python subprocess down cleanly.

- [ ] **Step 7: Commit**

```bash
git add desktop/renderer/src/pages/ desktop/renderer/src/App.tsx
git commit -m "feat(desktop): Dashboard (profile list) + Health page + CrashScreen"
```

---

## Task 8: Playwright e2e smoke

We need at least one e2e test that proves the wire: Electron launches, Python comes up, the renderer shows the profile grid. Since spawning real Electron + real Python is heavy, this test is opt-in (`npm run test:e2e`) and skipped from a default `pytest` run on the Python side.

**Files:**
- Create: `desktop/playwright.config.ts`
- Create: `desktop/tests/e2e/smoke.spec.ts`

- [ ] **Step 1: Playwright config**

Create `desktop/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  reporter: [["list"]],
  use: { headless: true },
});
```

- [ ] **Step 2: Install Playwright browser drivers**

```bash
cd desktop && npx playwright install chromium
```

(Playwright's "electron" support comes from the main Playwright package; no separate driver needed for Electron.)

- [ ] **Step 3: Write the smoke spec**

Create `desktop/tests/e2e/smoke.spec.ts`:

```ts
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

test("dashboard loads profiles from the spawned python service", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
    },
  });
  try {
    const window = await app.firstWindow();
    // The dashboard renders 15 profile cards once the backend is ready.
    await expect(window.locator("text=Profiles").first()).toBeVisible({ timeout: 30_000 });
    const cards = window.locator(".grid > div"); // Cards are direct children of the grid
    await expect(cards.first()).toBeVisible({ timeout: 30_000 });
    // At least 10 cards visible (we shipped 15; allow a margin)
    await expect(async () => {
      const count = await cards.count();
      expect(count).toBeGreaterThanOrEqual(10);
    }).toPass({ timeout: 30_000 });
  } finally {
    await app.close();
  }
});
```

- [ ] **Step 4: Build before running the spec**

```bash
cd desktop && npm run build
```

Expected: `dist-electron/main.js`, `dist-electron/preload.js`, `dist/index.html` produced.

- [ ] **Step 5: Run the spec**

```bash
cd desktop && npx playwright test
```

Expected: 1 passed. (Total runtime ~15–30 s — Electron launch + Python handshake + first render.)

If the test fails due to "service not ready" timeout, increase the inner timeout to 60_000 ms — the first launch can be slow if Playwright is downloading Chromium.

- [ ] **Step 6: Commit**

```bash
git add desktop/playwright.config.ts desktop/tests/
git commit -m "test(desktop): e2e smoke — Electron + Python + profile grid"
```

---

## Verification

After all tasks:

1. From the repo root in `devenv shell`:
   ```bash
   pytest tests/gui_service/   # Plan 1 tests still pass
   ```

2. From `desktop/`:
   ```bash
   npm run lint    # tsc -b --noEmit
   npm run build   # full build (electron main/preload + renderer)
   npx playwright test    # e2e smoke passes
   npm run dev     # interactive sanity check
   ```

3. Visual sanity:
   - Dashboard shows 15 profile cards.
   - Health page shows the 5 environment checks.
   - Quitting the app leaves no orphaned `python -m reverser.gui_service` processes.

## What this plan does NOT cover

Live engagement features land in Plan 3:

- `/api/sessions` (create / list / detail / state transitions).
- WebSocket `/ws/sessions/{id}` for the `AgentEvent` stream.
- Chat pane, tool timeline, KB browser, findings list, hypothesis tree.
- New-engagement wizard.
- F-key modals (skills, sudo, profile switch, stop).
- Auth-gate confirmation modal.
- Multi-engagement session sidebar (Phase 2).
- BloodHound graph, evidence gallery, scope editor (Phase 3).
- electron-builder packaging (Phase 4).
