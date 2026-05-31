/// <reference types="@testing-library/jest-dom" />
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SessionStatusBar } from "./SessionStatusBar";
import { getSessionStore, clearSessionStore } from "@/state/session-store";

vi.mock("@/api/queries", () => ({
  useSessions: vi.fn(() => ({
    data: {
      sessions: [
        {
          id: "s1", target: "10.129.6.125", target_name: "devhub",
          profile: "manager", state: "active", turns: 1, total_cost: 0,
          stopped_at: null, archived_at: null, backend: "claude",
          model: null, api_base: null, budget: 5, max_turns: 50,
        },
      ],
    },
  })),
  useProfiles: vi.fn(() => ({
    data: { profiles: [{ key: "manager", name: "Manager", domain: "network" }] },
  })),
}));

describe("SessionStatusBar active-dispatch chip", () => {
  it("shows the active dispatch specialty and sub-turn while running", () => {
    clearSessionStore("s1");
    const store = getSessionStore("s1");
    store.getState().ingest({ type: "status", phase: "running", turns: 1 } as never);
    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webrecon", sub_goal: "enumerate",
    });
    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 5,
      phase: "text", specialty: "webrecon", content: "x",
    });

    render(<SessionStatusBar sessionId="s1" />);

    expect(screen.getByText("webrecon")).toBeInTheDocument();
    expect(screen.getByText(/sub-turn 5/)).toBeInTheDocument();
  });

  it("does not show the chip when there is no active dispatch", () => {
    clearSessionStore("s2");
    const store = getSessionStore("s2");
    store.getState().ingest({ type: "status", phase: "running", turns: 1 } as never);
    render(<SessionStatusBar sessionId="s2" />);
    expect(screen.queryByText("webrecon")).not.toBeInTheDocument();
  });
});
