// desktop/renderer/src/panes/turn-bubble.test.tsx
/// <reference types="@testing-library/jest-dom" />
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
    llmStatus: null,
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

  it("renders LLM generation status on an active turn", () => {
    const turn = _makeTurn({
      status: "streaming",
      llmStatus: {
        phase: "generating",
        detail: "model output streaming",
        turn: 1,
        firstTokenMs: 750,
        generatedChars: 120,
        rateCharsPerSec: 300,
      },
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/first token 750ms/)).toBeInTheDocument();
    expect(screen.getByText(/120 chars/)).toBeInTheDocument();
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

  it("surfaces latest dispatch activity without expanding thinking", () => {
    const turn = _makeTurn({
      dispatches: new Map([["d1", {
        id: "d1",
        specialty: "ad",
        subGoal: "test smb",
        status: "running",
        subTurns: new Map([[0, {
          thinkingDeltas: ["Waiting for local backend slot (lmstudio)"],
          speechDeltas: [],
          toolCalls: [],
          toolResults: [],
        }]]),
      }]]),
      ordering: [{ kind: "dispatch", id: "d1" }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getAllByText(/queued on local backend/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Waiting for local backend slot/).length).toBeGreaterThan(0);
  });

  it("labels specialist activity separately from main agent activity", () => {
    const turn = _makeTurn({
      dispatches: new Map([["d1", {
        id: "d1",
        specialty: "ad",
        subGoal: "enumerate smb",
        status: "running",
        subTurns: new Map([[1, {
          thinkingDeltas: ["planning smb enum"],
          speechDeltas: ["Starting SMB checks"],
          toolCalls: [{ name: "", content: "nmap_scan {target:10.10.10.5}" }],
          toolResults: [{ ok: true, content: "445/tcp open smb" }],
        }]]),
      }]]),
      ordering: [{ kind: "dispatch", id: "d1" }],
    });
    render(<TurnBubble turn={turn} />);

    expect(screen.getByText(/AD sub-agent/i)).toBeInTheDocument();
    expect(screen.getByText(/specialist activity/i)).toBeInTheDocument();
    expect(screen.getByText(/nmap_scan/)).toBeInTheDocument();
    expect(screen.getAllByText(/445\/tcp open smb/).length).toBeGreaterThan(0);
  });
});
