import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProfilePickerModal } from "./ProfilePickerModal";

vi.mock("@/api/queries", () => ({
  useProfiles: vi.fn(() => ({
    data: {
      profiles: [
        { key: "manager", name: "Manager", description: "", skills: [], tools_allowlist: null },
        { key: "webpentest", name: "Web Pentest", description: "", skills: [], tools_allowlist: null },
      ],
    },
    isLoading: false,
  })),
  useUpdateSessionConfig: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("ProfilePickerModal", () => {
  it("lists available profiles", async () => {
    render(
      wrap(
        <ProfilePickerModal
          open
          onClose={() => {}}
          sessionId="s1"
          target="10.0.0.1"
          currentProfile="manager"
          sessionRunning={false}
        />,
      ),
    );
    await waitFor(() => expect(screen.getByText("Web Pentest")).toBeInTheDocument());
  });

  it("Apply is disabled when sessionRunning", async () => {
    render(
      wrap(
        <ProfilePickerModal
          open
          onClose={() => {}}
          sessionId="s1"
          target="10.0.0.1"
          currentProfile="manager"
          sessionRunning
        />,
      ),
    );
    await waitFor(() => expect(screen.getByText("Web Pentest")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Web Pentest"));
    expect(screen.getByRole("button", { name: /apply/i })).toBeDisabled();
  });
});
