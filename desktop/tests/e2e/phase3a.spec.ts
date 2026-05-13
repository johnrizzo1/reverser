import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// Phase 3a structural tests: confirm the build + critical scaffolding
// still works after the refactor (react-arborist import, session-store
// shape change, ChatPane three-stream merge). Real session-log replay
// assertions require a session-log fixture and are deferred.

test("sessions panel still renders after Phase 3a refactor", async () => {
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

test("targets panel still renders after Phase 3a refactor", async () => {
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
  } finally {
    await app.close();
  }
});

test("react-arborist import did not break the build (profiles grid still renders)", async () => {
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
    await expect(w.locator("text=Profiles").first()).toBeVisible({ timeout: 30_000 });
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
