import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SubTurnBubble } from "./SubTurnBubble";
import type { SubTurn } from "@/state/session-store";

function st(): SubTurn {
  return {
    thinkingDeltas: [], speechDeltas: [],
    toolCalls: [{ name: "", content: "nmap -sV 10.10.10.5" }],
    toolResults: [],
  };
}

describe("SubTurnBubble running tool", () => {
  it("marks the running tool with a 'running' tag when runningToolIndex matches", () => {
    render(<SubTurnBubble subTurn={st()} num={1} specialty="webrecon" runningToolIndex={0} />);
    expect(screen.getByText(/running/i)).toBeTruthy();
  });
  it("shows no 'running' tag when runningToolIndex is -1 (default)", () => {
    render(<SubTurnBubble subTurn={st()} num={1} specialty="webrecon" />);
    expect(screen.queryByText(/running/i)).toBeNull();
  });
});
