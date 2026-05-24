import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// When running outside a devenv shell the system PATH may only expose `python3`,
// not `python`. Prepend a tiny shim directory (tests/e2e/bin/python → python3)
// so the Electron main process can spawn the Python service via `python -m …`.
const shimBin = path.join(__dirname, "bin");

test("settings loads profiles from the spawned python service", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      PATH: `${shimBin}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const window = await app.firstWindow();
    // Sessions is the home page; wait for the SessionsPanel header.
    await expect(window.locator("text=Sessions").first()).toBeVisible({ timeout: 30_000 });
    // Profile cards moved to Settings; navigate there and verify.
    await window.locator('a[href="/settings"]').first().click();
    const cards = window.locator(".grid > div");
    await expect(cards.first()).toBeVisible({ timeout: 30_000 });
    await expect(async () => {
      const count = await cards.count();
      expect(count).toBeGreaterThanOrEqual(10);
    }).toPass({ timeout: 30_000 });
  } finally {
    await app.close();
  }
});
