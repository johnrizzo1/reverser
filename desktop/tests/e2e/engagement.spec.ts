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
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

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
