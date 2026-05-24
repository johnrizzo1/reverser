import { describe, it, expect } from "vitest";
import { makeSessionStore } from "./session-store";

describe("makeSessionStore", () => {
  it("initializes with empty state", () => {
    const store = makeSessionStore();
    const state = store.getState();
    expect(state.status).toBe("idle");
    expect(state.messages).toEqual([]);
  });
});
