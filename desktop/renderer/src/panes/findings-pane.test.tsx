/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { FindingsPane } from "./FindingsPane";
import { clearSessionStore } from "@/state/session-store";

const useTargetKBMock = vi.fn();

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
          turns: 1,
          total_cost: 0,
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
  useTargetKB: (target: string | null) => useTargetKBMock(target),
}));

vi.mock("@/state/targets-store", () => ({
  useTarget: vi.fn(() => ({
    data: {
      name: "reactor",
      primary_address_id: "addr-1",
      addresses: [
        {
          id: "addr-1",
          kind: "ip",
          value: "10.129.8.132",
          status: "active",
          added_at: "2026-05-30T00:00:00Z",
        },
      ],
    },
  })),
}));

vi.mock("@/components/FindingRow", () => ({
  FindingRow: ({ target }: { target: string | null }) => (
    <div>finding target: {target}</div>
  ),
}));

vi.mock("@/modals/ScreenshotLightboxModal", () => ({
  ScreenshotLightboxModal: () => null,
}));

describe("FindingsPane", () => {
  afterEach(() => {
    clearSessionStore("s1");
    useTargetKBMock.mockReset();
  });

  it("loads findings and screenshot badges using the primary address KB target", async () => {
    useTargetKBMock.mockReturnValue({
      data: {
        findings: [
          { id: 1, title: "XSS", severity: "high", description: "confirmed" },
        ],
      },
    });

    render(<FindingsPane sessionId="s1" />);

    await waitFor(() => {
      expect(screen.getByText("finding target: 10.129.8.132")).toBeInTheDocument();
    });
    expect(useTargetKBMock).toHaveBeenCalledWith("10.129.8.132");
    expect(useTargetKBMock).not.toHaveBeenCalledWith("reactor");
  });
});
