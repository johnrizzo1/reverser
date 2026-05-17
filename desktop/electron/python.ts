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
      // Drop any inherited PYTHONPATH — PyInstaller embeds the package
      // and a stale value can confuse the embedded interpreter.
      PYTHONPATH: undefined,
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
