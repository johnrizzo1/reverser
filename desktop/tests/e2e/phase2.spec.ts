import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

test("sessions panel: navigate /sessions and see the panel", async () => {
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
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    await w.click('[title="Sessions"]');

    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
    await expect(
      w.locator("text=Select a session from the panel on the left"),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel: navigate /targets and see the panel", async () => {
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
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    await w.click('[title="Targets"]');

    await expect(w.locator("text=Targets").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
    await expect(
      w.locator("text=Select a target from the panel on the left"),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("legacy /session/:id redirects to /sessions/:id", async () => {
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
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    await w.evaluate(() => {
      window.history.pushState({}, "", "/session/legacy-id");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await w.waitForFunction(
      () => window.location.pathname === "/sessions/legacy-id",
      { timeout: 5_000 },
    );
  } finally {
    await app.close();
  }
});
