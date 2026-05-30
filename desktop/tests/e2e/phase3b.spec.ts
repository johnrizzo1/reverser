import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// Phase 3b structural tests: confirm the new components mount without
// breaking the existing flow. Real fixture-driven assertions (Edit scope
// modal opens, Report tab renders content, screenshot lightbox opens)
// require a target directory with scope.toml/report content/screenshots —
// add those once we have a per-target test harness.

test("targets section still renders after Phase 3b refactor", async () => {
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
    await expect(w.locator("text=Targets").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("sessions panel still renders after Phase 3b refactor", async () => {
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
    await w.click('[title="Sessions"]');
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("profile grid still renders (react-markdown import didn't break)", async () => {
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
    const cards = w.locator(".grid > div");
    await expect(async () => {
      const count = await cards.count();
      expect(count).toBeGreaterThanOrEqual(10);
    }).toPass({ timeout: 30_000 });
  } finally {
    await app.close();
  }
});

test("legacy /session/:id still redirects (regression check)", async () => {
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
