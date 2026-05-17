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
