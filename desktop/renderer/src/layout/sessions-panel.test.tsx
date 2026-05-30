/// <reference types="@testing-library/jest-dom" />
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ActivityBar } from "./ActivityBar";
import { SessionsPanel } from "./SessionsPanel";

vi.mock("@/api/queries", () => ({
  useSessions: vi.fn(() => ({
    data: {
      sessions: [
        {
          id: "s1",
          target: "acme.local",
          profile: "manager",
          state: "active",
          turns: 2,
          total_cost: 1.25,
          stopped_at: null,
          archived_at: null,
          backend: "openai",
          model: "gpt-test",
          api_base: null,
          budget: 10,
          max_turns: 20,
        },
      ],
    },
  })),
  useTargets: vi.fn(() => ({
    data: {
      targets: [
        {
          name: "acme.local",
          has_kb: true,
          has_scope: true,
          archived: false,
        },
      ],
    },
  })),
  useArchiveTarget: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
  useUnarchiveTarget: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
  useDeleteTarget: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
  useArchiveSession: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
  useUnarchiveSession: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
  useDeleteSession: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
}));

describe("Sessions sidebar", () => {
  it("removes the separate targets activity tab", () => {
    render(
      <MemoryRouter initialEntries={["/sessions"]}>
        <ActivityBar />
      </MemoryRouter>,
    );

    expect(screen.queryByTitle("Targets")).not.toBeInTheDocument();
    expect(screen.getByTitle("Sessions")).toBeInTheDocument();
  });

  it("shows targets under sessions in the same sidebar column", () => {
    render(
      <MemoryRouter initialEntries={["/sessions"]}>
        <SessionsPanel />
      </MemoryRouter>,
    );

    const sessionsHeader = screen.getByText("Sessions");
    const targetsHeader = screen.getByText("Targets");
    expect(sessionsHeader.compareDocumentPosition(targetsHeader)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(screen.getAllByText("acme.local").length).toBeGreaterThanOrEqual(1);
  });
});
