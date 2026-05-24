import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// E2E: clicking the Archive icon on a session row opens the confirm modal,
// not the session detail view. Regression test for the z-index issue where
// the <a> Link wrapping the row was capturing clicks meant for the button.

const SESSION_ID = "e2e-archive-click";

test("Archive icon on session row opens the confirm modal", async () => {
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
    await expect(w.locator("text=Sessions").first()).toBeVisible({
      timeout: 30_000,
    });

    // Inject a fake "stopped" session so the Archive button isn't disabled
    // (archive is gated on liveActive=false).
    await w.evaluate((sessionId: string) => {
      const _realFetch = window.fetch.bind(window);
      const fakeSession = {
        id: sessionId,
        target: "/tmp/e2e-archive-target",
        profile: "general",
        state: "stopped",
        turns: 0,
        total_cost: 0,
        stopped_at: "2026-05-23T12:00:00+00:00",
        archived_at: null,
        backend: "anthropic",
        model: null,
        api_base: null,
        budget: 5,
        max_turns: 50,
      };
      window.fetch = async (input, init) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        if (url.includes("/api/sessions") && !url.includes("/api/sessions/")) {
          return new Response(
            JSON.stringify({ sessions: [fakeSession] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return _realFetch(input, init);
      };
      // Force the React Query cache to refetch with our mock.
      const qc = (window as unknown as { __getQueryClient?: () => unknown }).__getQueryClient?.();
      if (qc && typeof (qc as { invalidateQueries?: unknown }).invalidateQueries === "function") {
        (qc as { invalidateQueries: (opts: unknown) => Promise<void> }).invalidateQueries({
          queryKey: ["sessions"],
        });
      }
    }, SESSION_ID);

    // Wait for the row to render in the SessionsPanel.
    const rowTarget = w.getByText("/tmp/e2e-archive-target").first();
    await expect(rowTarget).toBeVisible({ timeout: 10_000 });

    // Hover the row to reveal the Archive button (opacity-0 → opacity-100).
    await rowTarget.hover();

    // Click the Archive button — it has a "title" attribute of "Archive".
    const archiveBtn = w.locator('button[title="Archive"]').first();
    await expect(archiveBtn).toBeVisible({ timeout: 5_000 });
    await archiveBtn.click();

    // The confirm modal should appear. If the click went to the Link instead,
    // we'd see navigation to /sessions/<id> and a SessionLayout with the
    // session id in the URL — no "Archive this session?" heading.
    await expect(w.locator("text=Archive this session?")).toBeVisible({
      timeout: 5_000,
    });
  } finally {
    await app.close();
  }
});
