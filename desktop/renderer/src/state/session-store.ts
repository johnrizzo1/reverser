import { create } from "zustand";

/**
 * WS frame shapes — keep aligned with src/reverser/gui_service/session_adapter.py
 * and docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md §3.3.
 */
export type WSFrame =
  | { type: "text"; role: "assistant"; delta: string }
  | { type: "thinking"; delta: string; redacted: boolean }
  | { type: "tool_call"; name: string; args: string }
  | { type: "tool_result"; ok: boolean; preview: string }
  | { type: "kb_update"; kind: string; row: unknown }
  | { type: "hypothesis"; action: string; row: HypothesisRow }
  | { type: "finding"; row: unknown }
  | { type: "dispatch"; specialty: string; phase: string; content: string }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

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
    ingest: () => set({}),
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
