/// <reference types="@testing-library/jest-dom" />
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBar } from "./StatusBar";

vi.mock("@/state/connection", () => ({
  useConnection: (selector: (state: { status: string; port: number }) => unknown) =>
    selector({ status: "ready", port: 60343 }),
}));

vi.mock("@/api/queries", () => ({
  useSessions: vi.fn(() => ({
    data: {
      sessions: [
        {
          id: "s1",
          target: "10.129.8.132",
          target_name: "reactor",
          profile: "webpentest",
          state: "active",
          turns: 3,
          total_cost: 0.42,
          stopped_at: null,
          archived_at: null,
          backend: "openai",
          model: null,
          api_base: null,
          budget: 5,
          max_turns: 50,
        },
      ],
    },
  })),
}));

describe("StatusBar", () => {
  it("shows the active engagement instead of the fallback label", () => {
    render(<StatusBar />);

    expect(screen.getByText("active engagement:")).toBeInTheDocument();
    expect(screen.getByText("reactor")).toBeInTheDocument();
    expect(screen.queryByText("no active engagement")).not.toBeInTheDocument();
  });
});
