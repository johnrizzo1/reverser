import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// E2E: Chat turn bubble + live KB update via injected WS frames.
//
// Strategy:
//   1. Launch and wait for the dashboard (backend up, query client ready).
//   2. Navigate to /sessions/<fake-id> — SessionLayout renders with an empty
//      store.  The session won't be in the DB so isActive=false (read-only),
//      but the ChatPane / HypothesesPane still mount and subscribe to the
//      session store.
//   3. Use window.__getSessionStore (exposed in main.tsx) to call
//      store.getState().ingest(frame) for each test frame directly in the
//      renderer process.  Zustand notifies subscribers synchronously, so the
//      DOM updates in the same task.
//   4. Assert the rendered elements.

const SESSION_ID = "e2e-chat-bubbles";

test("injected frames render turn bubbles and hypotheses pane", async () => {
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
    // Wait for dashboard — confirms the backend is up.
    await expect(w.locator("text=Sessions").first()).toBeVisible({
      timeout: 30_000,
    });

    // Navigate to the fake session.  The session won't exist in the DB, so
    // isActive=false, but the panes still render.
    await w.evaluate((sessionId: string) => {
      window.history.pushState({}, "", `/sessions/${sessionId}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, SESSION_ID);

    await w.waitForFunction(
      (id: string) => window.location.pathname === `/sessions/${id}`,
      SESSION_ID,
      { timeout: 5_000 },
    );

    // Wait for ChatPane to be in DOM (it renders immediately).
    await expect(
      w.locator("text=no messages yet — say hi to start"),
    ).toBeVisible({ timeout: 10_000 });

    // Inject frames into the session store via the window hook.
    await w.evaluate((sessionId: string) => {
      type StoreHook = (id: string) => { getState: () => { ingest: (f: unknown) => void } };
      const getStore = (window as unknown as { __getSessionStore?: StoreHook }).__getSessionStore;
      if (!getStore) throw new Error("__getSessionStore not found on window");
      const store = getStore(sessionId);
      const { ingest } = store.getState();

      // Frame sequence from the task spec.
      ingest({ type: "status", phase: "running", turns: 1 });
      ingest({ type: "thinking", delta: "considering", redacted: false, turn: 1 });
      ingest({ type: "text", role: "assistant", delta: "Hello", turn: 1 });
      ingest({
        type: "hypothesis",
        action: "create",
        row: {
          id: 1,
          parent_id: null,
          statement: "test",
          status: "proposed",
        },
      });
    }, SESSION_ID);

    // --- Assertions ---

    // "thinking" chip should appear (ThinkingChip renders a button with the
    // text "thinking [show N]").
    await expect(
      w.locator("button:has-text('thinking')"),
    ).toBeVisible({ timeout: 5_000 });

    // "Hello" text should be visible in the SpeechBlock.
    await expect(w.locator("text=Hello")).toBeVisible({ timeout: 5_000 });

    // Navigate to the Hypotheses tab (it is the default, but confirm).
    // The tab button text is "hypotheses".
    const hypothesesTabButton = w.locator("button", { hasText: "hypotheses" }).first();
    // It may already be active; click it to be safe.
    await hypothesesTabButton.click();

    // "test" hypothesis statement should be visible in the tree.
    await expect(w.locator("text=test").first()).toBeVisible({
      timeout: 5_000,
    });
  } finally {
    await app.close();
  }
});
