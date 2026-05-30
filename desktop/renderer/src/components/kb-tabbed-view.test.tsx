/// <reference types="@testing-library/jest-dom" />
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { KBTabbedView } from "./KBTabbedView";

vi.mock("@/api/queries", () => ({
  useTargetKB: vi.fn(() => ({
    isLoading: false,
    data: {
      findings: [],
      hypotheses: [],
      hosts: [],
      services: [
        {
          host_ip: "10.129.8.132",
          port: 443,
          proto: "tcp",
          service: "https",
          version: "nginx 1.24.0 with a deliberately long banner value",
          banner: "HTTP/1.1 200 OK Server: nginx X-Powered-By: example framework",
          scan_source: "nmap -sV",
        },
      ],
      credentials: [],
      artifacts: [],
      notes: [],
    },
  })),
}));

vi.mock("@/panes/ReportTab", () => ({
  ReportTab: () => null,
}));

describe("KBTabbedView", () => {
  it("renders service rows as wrapped labeled fields instead of truncated JSON", () => {
    const { container } = render(<KBTabbedView target="reactor" />);

    fireEvent.click(screen.getByRole("button", { name: /services \(1\)/i }));

    expect(screen.getByText("10.129.8.132:443/tcp")).toBeInTheDocument();
    expect(screen.getByText("https")).toBeInTheDocument();
    expect(screen.getAllByText(/nginx 1\.24\.0/).length).toBeGreaterThan(0);
    expect(screen.getByText(/HTTP\/1\.1 200 OK/)).toBeInTheDocument();
    expect(container.querySelector("pre")).not.toBeInTheDocument();
    expect(container.querySelector(".truncate")).not.toBeInTheDocument();
  });
});
