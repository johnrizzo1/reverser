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

/** Build the env we'll hand the spawned Python process: inherits the
 *  caller env, then prepends <projectRoot>/src to PYTHONPATH so `reverser`
 *  is importable without requiring `pip install -e .`. This matches the
 *  pytest config (`pythonpath = ["src"]`) and works regardless of whether
 *  the user is inside `devenv shell`. */
function buildPythonEnv(projectRoot: string): NodeJS.ProcessEnv {
  const srcDir = path.join(projectRoot, "src");
  const existing = process.env.PYTHONPATH ?? "";
  const sep = process.platform === "win32" ? ";" : ":";
  const pythonpath = existing ? `${srcDir}${sep}${existing}` : srcDir;
  return { ...process.env, PYTHONPATH: pythonpath };
}

/** Pick the first Python interpreter on PATH that can import the gui_service
 *  module. Tries `python` then `python3`. Returns null if neither works. */
function findPython(projectRoot: string): { cmd: string; reason: string } | null {
  const env = buildPythonEnv(projectRoot);
  let lastReason = "";
  for (const cmd of ["python", "python3"]) {
    try {
      // -c "import reverser.gui_service" verifies both that the interpreter
      // exists AND that the project deps are available (with PYTHONPATH=src
      // prepended, the project itself is reachable; this catches missing
      // runtime deps like fastapi).
      const r = spawnSync(cmd, ["-c", "import reverser.gui_service"], {
        cwd: projectRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
        encoding: "utf8",
      });
      if (r.status === 0) return { cmd, reason: "" };
      // Found the binary but the import failed — capture stderr; keep
      // trying the next candidate in case it's the one that works.
      if (r.status !== null) {
        lastReason = `${cmd} -c "import reverser.gui_service" failed:\n${r.stderr.trim()}`;
      }
    } catch {
      // ENOENT — try the next candidate.
    }
  }
  return lastReason ? { cmd: "", reason: lastReason } : null;
}


export class PythonSupervisor {
  private proc: ChildProcess | null = null;
  private exited = false;

  constructor(private opts: SupervisorOptions) {}

  start(): void {
    if (this.proc) throw new Error("already started");

    const probe = findPython(this.opts.projectRoot);
    if (!probe || !probe.cmd) {
      const reason = probe?.reason
        ? probe.reason
        : "neither 'python' nor 'python3' is on PATH — run from inside `devenv shell`";
      this.exited = true;
      // Defer the callback so the caller can finish wiring before it fires.
      setImmediate(() => this.opts.onExit({ code: null, signal: null, reason }));
      return;
    }

    // The interpreter we picked is reachable AND can import the project module.
    // Reuse the same env (PYTHONPATH=src) so the spawn behaves identically
    // to the probe.
    const proc = spawn(
      probe.cmd,
      ["-u", "-m", "reverser.gui_service",
       "--host", "127.0.0.1", "--port", "0",
       "--project-root", this.opts.projectRoot],
      {
        cwd: this.opts.projectRoot,
        env: buildPythonEnv(this.opts.projectRoot),
        stdio: ["ignore", "pipe", "pipe"],
      }
    );

    this.proc = proc;

    const stdoutRl = readline.createInterface({ input: proc.stdout! });
    let handshakeSeen = false;
    // Buffer stderr so a pre-handshake crash can carry the actual error
    // into the exit reason (without it, the user just sees "code=1").
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
        : `service died before handshake (code=${code}, signal=${signal}) using ${probe.cmd}` +
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

/** Resolve the project root. In dev, that's the parent of the desktop/ dir.
 *  app.getAppPath() returns the directory containing package.json (desktop/),
 *  so one level up is the project root. */
export function defaultProjectRoot(): string {
  return path.resolve(app.getAppPath(), "..");
}
