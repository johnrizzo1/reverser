// desktop/renderer/src/panes/turn-bubble.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TurnBubble } from "./TurnBubble";
import type { Turn } from "@/state/session-store";

function _makeTurn(overrides: Partial<Turn>): Turn {
  return {
    turn: 1,
    thinkingDeltas: [],
    speechDeltas: [],
    toolCalls: new Map(),
    dispatches: new Map(),
    status: "done",
    ordering: [],
    ...overrides,
  };
}

describe("TurnBubble", () => {
  it("renders speech in order", () => {
    const turn = _makeTurn({
      speechDeltas: ["Hello world"],
      ordering: [{ kind: "speech", index: 0 }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/Hello world/)).toBeInTheDocument();
  });

  it("renders thinking chip collapsed by default", () => {
    const turn = _makeTurn({
      thinkingDeltas: ["hmm"],
      ordering: [{ kind: "thinking", index: 0 }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.queryByText(/hmm/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/thinking/));
    expect(screen.getByText(/hmm/)).toBeInTheDocument();
  });

  it("renders a tool call chip with the tool name", () => {
    const turn = _makeTurn({
      toolCalls: new Map([["tu_1", { id: "tu_1", name: "bash", args: '{"cmd":"ls"}' }]]),
      ordering: [{ kind: "tool", id: "tu_1" }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/bash/)).toBeInTheDocument();
  });

  it("renders a dispatch panel for a dispatch entry", () => {
    const turn = _makeTurn({
      dispatches: new Map([["d1", {
        id: "d1", specialty: "webpentest", subGoal: "test xss",
        status: "running", subTurns: new Map(),
      }]]),
      ordering: [{ kind: "dispatch", id: "d1" }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/webpentest/)).toBeInTheDocument();
  });
});
