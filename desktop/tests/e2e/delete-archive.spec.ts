import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// Plan 8 structural tests: confirm the new UI elements mount without
// breaking the existing flow. Real fixture-driven assertions (clicking
// Archive opens the modal and the row disappears from the default list)
// require a pre-seeded targets directory and an attached service — out
// of scope for these structural tests.

test("sessions panel renders the archived filter tab", async () => {
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
    await expect(w.locator("text=/^archived \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel renders the Show archived toggle", async () => {
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
    await w.click('[title="Targets"]');
    await expect(w.locator("text=Show archived")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("sessions panel still shows the all filter (regression)", async () => {
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
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel still shows the by activity sort (regression)", async () => {
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
    await w.click('[title="Targets"]');
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});
