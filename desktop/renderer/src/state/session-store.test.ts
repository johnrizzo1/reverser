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

describe("ingest text frames", () => {
  it("creates a turn and appends speech delta with a speech ordering entry", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "text", role: "assistant", delta: "Hi ", turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.speechDeltas).toEqual(["Hi "]);
    expect(t.ordering).toEqual([{ kind: "speech", index: 0 }]);
  });

  it("appends to existing speech ordering entry when consecutive", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "text", role: "assistant", delta: "Hi ", turn: 1 });
    store.getState().ingest({ type: "text", role: "assistant", delta: "there", turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.speechDeltas).toEqual(["Hi ", "there"]);
    expect(t.ordering).toEqual([{ kind: "speech", index: 0 }]);
  });
});

describe("ingest thinking frames", () => {
  it("creates a turn and appends thinking delta with a thinking ordering entry", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "thinking", delta: "hmm", redacted: false, turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.thinkingDeltas).toEqual(["hmm"]);
    expect(t.ordering).toEqual([{ kind: "thinking", index: 0 }]);
  });
});

describe("ingest tool_call/tool_result", () => {
  it("creates a ToolCall keyed by tool_use_id and pairs the result", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "tool_call", name: "bash", args: '{"cmd":"ls"}',
      tool_use_id: "tu_1", turn: 1,
    });
    store.getState().ingest({
      type: "tool_result", ok: true, preview: "file.txt",
      tool_use_id: "tu_1", turn: 1,
    });
    const t = store.getState().turns.get(1)!;
    const tc = t.toolCalls.get("tu_1")!;
    expect(tc.name).toBe("bash");
    expect(tc.result).toEqual({ ok: true, preview: "file.txt" });
    expect(t.ordering).toEqual([{ kind: "tool", id: "tu_1" }]);
  });

  it("drops a tool_result with unknown tool_use_id", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "tool_result", ok: true, preview: "x",
      tool_use_id: "unknown", turn: 1,
    });
    const t = store.getState().turns.get(1);
    expect(t?.toolCalls.size ?? 0).toBe(0);
  });
});

describe("ingest status frames", () => {
  it("advances currentTurn on status running", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    expect(store.getState().currentTurn).toBe(1);
    expect(store.getState().turns.get(1)?.status).toBe("streaming");

    store.getState().ingest({ type: "status", phase: "running", turns: 2 });
    expect(store.getState().currentTurn).toBe(2);
    expect(store.getState().turns.get(1)?.status).toBe("done");
  });

  it("marks current turn done on awaiting_input", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    store.getState().ingest({ type: "status", phase: "awaiting_input" });
    expect(store.getState().turns.get(1)?.status).toBe("done");
    expect(store.getState().status).toBe("awaiting_input");
  });
});

describe("ingest dispatch frames", () => {
  it("start creates a Dispatch on the parent turn", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "test xss", hypothesis_id: 4,
    });
    const t = store.getState().turns.get(1)!;
    const d = t.dispatches.get("d1")!;
    expect(d.specialty).toBe("webpentest");
    expect(d.subGoal).toBe("test xss");
    expect(d.hypothesisId).toBe(4);
    expect(d.status).toBe("running");
    expect(t.ordering).toEqual([{ kind: "dispatch", id: "d1" }]);
  });

  it("text/thinking/tool_call drill into the sub-turn", () => {
    const store = makeSessionStore();
    const ingest = store.getState().ingest;
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "x" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 1,
      phase: "thinking", specialty: "webpentest", content: "scoping" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 1,
      phase: "text", specialty: "webpentest", content: "starting" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 2,
      phase: "tool_call", specialty: "webpentest", content: "bash ls" });

    const d = store.getState().turns.get(1)!.dispatches.get("d1")!;
    const st1 = d.subTurns.get(1)!;
    const st2 = d.subTurns.get(2)!;
    expect(st1.thinkingDeltas).toEqual(["scoping"]);
    expect(st1.speechDeltas).toEqual(["starting"]);
    expect(st2.toolCalls.length).toBe(1);
    expect(st2.toolCalls[0].content).toBe("bash ls");
  });

  it("end sets status/cost/turnsConsumed on the Dispatch", () => {
    const store = makeSessionStore();
    const ingest = store.getState().ingest;
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "x" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "end",
      specialty: "webpentest", status: "completed", cost: 0.53, turns: 4 });

    const d = store.getState().turns.get(1)!.dispatches.get("d1")!;
    expect(d.status).toBe("completed");
    expect(d.cost).toBe(0.53);
    expect(d.turnsConsumed).toBe(4);
  });

  it("end without start is dropped silently", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "dispatch", dispatch_id: "ghost", turn: 1, phase: "end",
      specialty: "x", status: "completed", cost: 0, turns: 0,
    });
    expect(store.getState().turns.get(1)?.dispatches.size ?? 0).toBe(0);
  });
});
