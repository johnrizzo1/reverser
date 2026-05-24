import { describe, it, expect, beforeEach } from "vitest";
import { makeSessionStore } from "./session-store";

describe("session-store new shape", () => {
  let store: ReturnType<typeof makeSessionStore>;

  beforeEach(() => {
    store = makeSessionStore();
  });

  it("initializes with empty turns Map and currentTurn=0", () => {
    const s = store.getState();
    expect(s.turns).toBeInstanceOf(Map);
    expect(s.turns.size).toBe(0);
    expect(s.currentTurn).toBe(0);
    expect(s.findings).toBeInstanceOf(Map);
    expect(s.findings.size).toBe(0);
  });

  it("no longer exposes messages or pendingAssistantText", () => {
    const s = store.getState() as Record<string, unknown>;
    expect(s.messages).toBeUndefined();
    expect(s.pendingAssistantText).toBeUndefined();
    expect(s.thinkingEntries).toBeUndefined();
    expect(s.dispatchEntries).toBeUndefined();
  });
});
