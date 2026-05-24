import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";
import fs from "fs/promises";
import os from "os";

test("new engagement wizard loads and form interactions work", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      // Match the python shim used in smoke.spec.ts so python -m reverser
      // resolves to python3 on systems without `python` on PATH.
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    // Dashboard renders first
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 30_000 });

    // Click the CTA
    await w.click("text=New engagement");

    // Wizard appears
    await expect(w.locator("text=Path or URL")).toBeVisible({ timeout: 10_000 });

    // Fill a throwaway target path
    const tmpBinary = path.join(os.tmpdir(), `reverser-e2e-${Date.now()}.bin`);
    await fs.writeFile(tmpBinary, "stub");
    await w.fill("input[placeholder*='binary']", tmpBinary);

    // Start button enables after filling target
    await expect(w.locator("button:has-text('Start engagement')")).toBeEnabled();

    // We DON'T click Start — that requires a real backend with API key.
    // The wizard rendering + form interaction is the smoke we care about.
  } finally {
    await app.close();
  }
});

// Smoke test for the config panel wiring: verifies the chevron toggle in
// SessionStatusBar is in place and that the panel renders when expanded.
// The full PATCH round-trip (edit → save → re-read) is covered by backend
// tests in tests/gui_service/test_session_config_routes.py — we can't run
// it here because starting a real engagement requires an API key + backend.
test("session status bar exposes a config-toggle chevron", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 30_000 });

    // Navigate to a session URL without going through the New Engagement
    // flow — the legacy /session/:id redirect into /sessions/:id renders
    // SessionLayout with no row data, which is enough to prove the chevron
    // exists.
    await w.evaluate(() => {
      window.history.pushState({}, "", "/session/config-smoke");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await w.waitForFunction(
      () => window.location.pathname === "/sessions/config-smoke",
      { timeout: 5_000 },
    );

    // Chevron starts collapsed → aria-label "Show config"
    const chevron = w.locator('button[aria-label="Show config"]');
    await expect(chevron).toBeVisible({ timeout: 10_000 });

    // Clicking toggles to "Hide config" — proves the state wiring works.
    await chevron.click();
    await expect(
      w.locator('button[aria-label="Hide config"]'),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});
