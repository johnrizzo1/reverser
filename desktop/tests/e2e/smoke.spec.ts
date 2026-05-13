import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// When running outside a devenv shell the system PATH may only expose `python3`,
// not `python`. Prepend a tiny shim directory (tests/e2e/bin/python → python3)
// so the Electron main process can spawn the Python service via `python -m …`.
const shimBin = path.join(__dirname, "bin");

test("dashboard loads profiles from the spawned python service", async () => {
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
