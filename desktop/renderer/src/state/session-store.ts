import { create } from "zustand";

/**
 * WS frame shapes — keep aligned with src/reverser/gui_service/session_adapter.py
 * and docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md §3.3.
 */
export type WSFrame =
  | { type: "text"; role: "assistant"; delta: string; turn: number }
  | { type: "thinking"; delta: string; redacted: boolean; turn: number }
  | { type: "tool_call"; name: string; args: string; tool_use_id: string; turn: number }
  | { type: "tool_result"; ok: boolean; preview: string; tool_use_id: string; turn: number }
  | DispatchFrame
  | { type: "hypothesis"; action: "create" | "update"; row: HypothesisRow }
  | { type: "finding"; action: "create" | "update"; row: FindingRow }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

export type DispatchFrame =
  | { type: "dispatch"; dispatch_id: string; turn: number; phase: "start";
      specialty: string; hypothesis_id?: number; sub_goal: string }
  | { type: "dispatch"; dispatch_id: string; turn: number; phase: "end";
      specialty: string; status: string; cost: number; turns: number }
  | { type: "dispatch"; dispatch_id: string; turn: number; sub_turn: number;
      phase: "text" | "thinking" | "tool_call" | "tool_result" | "tool_error";
      specialty: string; content: string };

export type ToolCall = {
  id: string;                          // tool_use_id from backend
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
};

export type SubTurn = {
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: { name: string; content: string }[];
  toolResults: { ok: boolean; content: string }[];
};

export type Dispatch = {
  id: string;
  specialty: string;
  hypothesisId?: number;
  subGoal: string;
  status: "running" | "completed" | "error";
  cost?: number;
  turnsConsumed?: number;
  subTurns: Map<number, SubTurn>;
};

export type TurnOrderingEntry =
  | { kind: "thinking"; index: number }
  | { kind: "speech"; index: number }
  | { kind: "tool"; id: string }
  | { kind: "dispatch"; id: string };

export type Turn = {
  turn: number;
  userMessage?: string;
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: Map<string, ToolCall>;
  dispatches: Map<string, Dispatch>;
  status: "streaming" | "done";
  ordering: TurnOrderingEntry[];
};

export type FindingRow = {
  id: number;
  target?: string;
  finding?: string;
  severity?: string | null;
  evidence?: string | null;
  refs?: unknown[] | null;
  created_at?: string | null;
  updated_at?: string | null;
};

// HypothesisRow mirrors HypothesisFact from src/reverser/kb/store.py
export type HypothesisRow = {
  id: number;
  parent_id: number | null;
  statement: string;
  rationale?: string | null;
  status: "proposed" | "testing" | "confirmed" | "refuted" | "abandoned" | string;
  confidence?: number | null;
  dispatched_to?: string | null;
  dispatch_count?: number;
  evidence_refs?: unknown[] | null;
  tags?: string[] | null;
  created_at?: string | null;
  updated_at?: string | null;
};

// Log event shape from /api/sessions/log/{id}.
type LogEventInput =
  | { kind: "thinking"; content: string; ts: string | null }
  | { kind: "tool_call"; name: string; input: string; ts: string | null }
  | { kind: "tool_result"; ok: boolean; preview: string; ts: string | null }
  | { kind: "dispatch"; specialty: string; phase: string; content: string; ts: string | null };

export type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  turns: Map<number, Turn>;
  currentTurn: number;
  hypotheses: Map<number, HypothesisRow>;
  findings: Map<number, FindingRow>;
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
  replayed: boolean;
};

type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
  seedFromSessionLog: (events: LogEventInput[]) => void;
  seedHypotheses: (rows: HypothesisRow[]) => void;
  seedFindings: (rows: FindingRow[]) => void;
};

function _getOrCreateTurn(turns: Map<number, Turn>, turn: number): Turn {
  let t = turns.get(turn);
  if (!t) {
    t = {
      turn,
      thinkingDeltas: [],
      speechDeltas: [],
      toolCalls: new Map(),
      dispatches: new Map(),
      status: "streaming",
      ordering: [],
    };
    turns.set(turn, t);
  }
  return t;
}

const _initialState = (): SessionState => ({
  status: "idle",
  turns: new Map(),
  currentTurn: 0,
  hypotheses: new Map(),
  findings: new Map(),
  budget: null,
  connBreakerTripped: false,
  log: [],
  replayed: false,
});

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),
    reset: () => set(_initialState()),
    appendUserMessage: () => set({}),
    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            t.speechDeltas = [...t.speechDeltas, frame.delta];
            const last = t.ordering.at(-1);
            if (!last || last.kind !== "speech") {
              t.ordering = [...t.ordering, { kind: "speech", index: t.speechDeltas.length - 1 }];
            }
            return { turns };
          }
          case "thinking": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            t.thinkingDeltas = [...t.thinkingDeltas, frame.delta];
            const last = t.ordering.at(-1);
            if (!last || last.kind !== "thinking") {
              t.ordering = [...t.ordering, { kind: "thinking", index: t.thinkingDeltas.length - 1 }];
            }
            return { turns };
          }
          case "tool_call": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            const tc: ToolCall = {
              id: frame.tool_use_id, name: frame.name, args: frame.args,
            };
            t.toolCalls = new Map(t.toolCalls);
            t.toolCalls.set(frame.tool_use_id, tc);
            t.ordering = [...t.ordering, { kind: "tool", id: frame.tool_use_id }];
            return { turns };
          }
          case "tool_result": {
            const turns = new Map(s.turns);
            const t = turns.get(frame.turn);
            if (!t) return {};
            const tc = t.toolCalls.get(frame.tool_use_id);
            if (!tc) {
              console.warn("tool_result for unknown tool_use_id", frame.tool_use_id);
              return {};
            }
            t.toolCalls = new Map(t.toolCalls);
            t.toolCalls.set(frame.tool_use_id, {
              ...tc, result: { ok: frame.ok, preview: frame.preview },
            });
            return { turns };
          }
          case "status": {
            const next: Partial<SessionState> = { status: frame.phase as SessionState["status"] };
            if (frame.phase === "running" && typeof frame.turns === "number") {
              const turns = new Map(s.turns);
              const prev = turns.get(s.currentTurn);
              if (prev && prev.status === "streaming") {
                turns.set(s.currentTurn, { ...prev, status: "done" });
              }
              _getOrCreateTurn(turns, frame.turns);
              next.turns = turns;
              next.currentTurn = frame.turns;
            } else if (["awaiting_input", "stopped", "completed", "error"].includes(frame.phase)) {
              const turns = new Map(s.turns);
              const cur = turns.get(s.currentTurn);
              if (cur && cur.status === "streaming") {
                turns.set(s.currentTurn, { ...cur, status: "done" });
                next.turns = turns;
              }
            }
            return next;
          }
          case "budget":
            return { budget: { spent: frame.spent, remaining: frame.remaining, turn: frame.turn } };
          case "conn_breaker":
            return { connBreakerTripped: frame.tripped };
          case "log":
            return { log: [...s.log.slice(-499), { level: frame.level, msg: frame.msg, ts: Date.now() }] };
          case "hypothesis": {
            const m = new Map(s.hypotheses);
            m.set(frame.row.id, frame.row);
            return { hypotheses: m };
          }
          case "finding": {
            const m = new Map(s.findings);
            m.set(frame.row.id, frame.row);
            return { findings: m };
          }
          case "dispatch": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            const dispatches = new Map(t.dispatches);

            if (frame.phase === "start") {
              dispatches.set(frame.dispatch_id, {
                id: frame.dispatch_id,
                specialty: frame.specialty,
                hypothesisId: frame.hypothesis_id,
                subGoal: frame.sub_goal,
                status: "running",
                subTurns: new Map(),
              });
              t.dispatches = dispatches;
              t.ordering = [...t.ordering, { kind: "dispatch", id: frame.dispatch_id }];
              return { turns };
            }

            if (frame.phase === "end") {
              const d = dispatches.get(frame.dispatch_id);
              if (!d) {
                console.warn("dispatch end for unknown dispatch_id", frame.dispatch_id);
                return {};
              }
              dispatches.set(frame.dispatch_id, {
                ...d,
                status: frame.status === "completed" ? "completed" : "error",
                cost: frame.cost,
                turnsConsumed: frame.turns,
              });
              t.dispatches = dispatches;
              return { turns };
            }

            // sub-turn event
            const d = dispatches.get(frame.dispatch_id);
            if (!d) {
              console.warn("dispatch event for unknown dispatch_id", frame.dispatch_id);
              return {};
            }
            const subTurns = new Map(d.subTurns);
            let st = subTurns.get(frame.sub_turn);
            if (!st) {
              st = { thinkingDeltas: [], speechDeltas: [], toolCalls: [], toolResults: [] };
            } else {
              st = { ...st };
            }
            if (frame.phase === "thinking") st.thinkingDeltas = [...st.thinkingDeltas, frame.content];
            else if (frame.phase === "text") st.speechDeltas = [...st.speechDeltas, frame.content];
            else if (frame.phase === "tool_call") st.toolCalls = [...st.toolCalls, { name: "", content: frame.content }];
            else if (frame.phase === "tool_result") st.toolResults = [...st.toolResults, { ok: true, content: frame.content }];
            else if (frame.phase === "tool_error") st.toolResults = [...st.toolResults, { ok: false, content: frame.content }];
            subTurns.set(frame.sub_turn, st);

            dispatches.set(frame.dispatch_id, { ...d, subTurns });
            t.dispatches = dispatches;
            return { turns };
          }
          default:
            return {};
        }
      }),
    seedFromSessionLog: () => set({ replayed: true }),
    seedHypotheses: (rows) => set(() => {
      const m = new Map<number, HypothesisRow>();
      for (const r of rows) m.set(r.id, r);
      return { hypotheses: m };
    }),
    seedFindings: (rows) => set(() => {
      const m = new Map<number, FindingRow>();
      for (const r of rows) m.set(r.id, r);
      return { findings: m };
    }),
  }));

const _stores = new Map<string, ReturnType<typeof makeSessionStore>>();

export function getSessionStore(sessionId: string) {
  let s = _stores.get(sessionId);
  if (!s) {
    s = makeSessionStore();
    _stores.set(sessionId, s);
  }
  return s;
}

export function clearSessionStore(sessionId: string) {
  _stores.delete(sessionId);
}
