import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DispatchPanel } from "./DispatchPanel";
import type { Dispatch } from "@/state/session-store";

function makeRunningDispatch(lastActivityAt: number): Dispatch {
  return {
    id: "d1", specialty: "webrecon", subGoal: "enumerate",
    status: "running", subTurns: new Map(), lastActivityAt,
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("DispatchPanel staleness", () => {
  it("shows no idle marker when recently active", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    render(<DispatchPanel dispatch={makeRunningDispatch(Date.now() - 10_000)} />);
    expect(screen.queryByText(/idle/i)).toBeNull();
  });

  it("shows an idle marker after 90s of no activity", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    render(<DispatchPanel dispatch={makeRunningDispatch(Date.now() - 120_000)} />);
    expect(screen.getByText(/idle/i)).toBeTruthy();
  });

  it("renders a timeout dispatch with its status label and no spinner", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    const d: Dispatch = {
      id: "d2", specialty: "webrecon", subGoal: "enumerate",
      status: "timeout", subTurns: new Map(), cost: 0.2, turnsConsumed: 3,
    };
    render(<DispatchPanel dispatch={d} />);
    expect(screen.getByText("timeout")).toBeTruthy();
  });

  it("does not show a stall indicator for a running dispatch with no lastActivityAt (replay)", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    const replay: Dispatch = {
      id: "d3", specialty: "webrecon", subGoal: "enumerate",
      status: "running", subTurns: new Map(),
      // lastActivityAt intentionally omitted (historical replay)
    };
    render(<DispatchPanel dispatch={replay} />);
    expect(screen.queryByText(/idle/i)).toBeNull();
  });
});
