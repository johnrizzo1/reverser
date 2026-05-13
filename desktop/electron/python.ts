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

/** Resolve the project root. In dev, that's the parent of the desktop/ dir.
 *  app.getAppPath() returns the directory containing package.json (desktop/),
 *  so one level up is the project root. */
export function defaultProjectRoot(): string {
  return path.resolve(app.getAppPath(), "..");
}
