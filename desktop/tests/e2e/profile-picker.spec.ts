import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// E2E: F2 opens the profile picker modal inside an active SessionLayout.
//
// Reaching "isActive" without a real backend requires injecting a fake session
// into the React Query cache via the __getQueryClient hook exposed in main.tsx.
// The pattern is:
//   1. Launch the app and wait for the dashboard (backend ready).
//   2. Read port + token from __reverserConnection so we can intercept fetch.
//   3. Mock window.fetch to return a fake "active" session for any
//      /api/sessions request.  All other requests pass through to the real
//      backend so the shell keeps working.
//   4. Navigate to /sessions/<fake-id>.
//   5. Force a React Query refetch so the mock data lands in the cache and
//      isActive becomes true — this makes the F2 keydown handler register.
//   6. Press F2 and assert "Switch profile" heading is visible.
//   7. Click Cancel and assert the heading is gone.

const SESSION_ID = "e2e-profile-picker";

test("F2 opens profile picker modal", async () => {
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
    // Wait for dashboard — confirms backend is up and query client is ready.
    await expect(w.locator("text=Dashboard").first()).toBeVisible({
      timeout: 30_000,
    });

    // Inject a fetch mock that returns our fake "active" session for any
    // /api/sessions path, and passes all other requests through.
    await w.evaluate((sessionId: string) => {
      const _realFetch = window.fetch.bind(window);
      const fakeSession = {
        id: sessionId,
        target: "/tmp/e2e-target",
        profile: "default",
        state: "active",
        turns: 0,
        total_cost: 0,
        stopped_at: null,
        archived_at: null,
        backend: "anthropic",
        model: null,
        api_base: null,
        budget: 5,
        max_turns: 50,
      };

      window.fetch = async (input, init) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        // Intercept sessions list endpoint
        if (url.includes("/api/sessions") && !url.includes("/api/sessions/")) {
          return new Response(
            JSON.stringify({ sessions: [fakeSession] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        // Intercept individual session config endpoint to avoid 404 errors
        if (url.includes(`/api/sessions/${sessionId}`)) {
          return new Response(
            JSON.stringify(fakeSession),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        // Profiles endpoint — needed for the modal to render its list
        // Pass through; the real backend will serve real profiles.
        return _realFetch(input, init);
      };
    }, SESSION_ID);

    // Navigate into the fake session.
    await w.evaluate((sessionId: string) => {
      window.history.pushState({}, "", `/sessions/${sessionId}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, SESSION_ID);

    await w.waitForFunction(
      (id: string) => window.location.pathname === `/sessions/${id}`,
      SESSION_ID,
      { timeout: 5_000 },
    );

    // Invalidate the sessions query so React Query re-fetches with our mock.
    await w.evaluate(() => {
      const qc = (
        window as unknown as { __getQueryClient?: () => { invalidateQueries: (o: object) => void } }
      ).__getQueryClient?.();
      qc?.invalidateQueries({ queryKey: ["sessions"] });
    });

    // Wait until SessionLayout detects the session as active: the bottom bar
    // shows the "Profile (F2)" button only when isActive is true.
    await expect(w.locator("button:has-text('Profile (F2)')")).toBeVisible({
      timeout: 10_000,
    });

    // Press F2 — the keydown handler calls setProfileOpen(true).
    await w.keyboard.press("F2");

    // Assert the modal heading is visible within 5 s.
    await expect(w.locator("h2:has-text('Switch profile')")).toBeVisible({
      timeout: 5_000,
    });

    // Click Cancel and assert the modal is gone.
    await w.click("button:has-text('Cancel')");
    await expect(w.locator("h2:has-text('Switch profile')")).toBeHidden({
      timeout: 5_000,
    });
  } finally {
    await app.close();
  }
});
