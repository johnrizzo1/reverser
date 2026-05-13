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
  | { type: "dispatch"; specialist: string; child_session_id: string; phase: string }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

export type ChatMessage =
  | { role: "user"; text: string; turn?: number }
  | { role: "assistant"; text: string; turn?: number };

export type ToolCall = {
  id: string;
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
  startedAt: number;
};

export type ThinkingEntry = {
  text: string;
  turn?: number;
  ts?: string | null;
};

export type DispatchEntry = {
  specialty: string;
  phase: string;
  content: string;
  turn?: number;
  ts?: string | null;
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
  messages: ChatMessage[];
  pendingAssistantText: string;
  toolCalls: ToolCall[];
  thinkingEntries: ThinkingEntry[];
  dispatchEntries: DispatchEntry[];
  /** Hypotheses keyed by id. The HypothesesPane builds the tree from
   *  parent_id on render. Live WS updates overwrite-by-id; the initial
   *  KB seed populates this map on mount. */
  hypotheses: Map<number, HypothesisRow>;
  findings: unknown[];
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
  /** True once a session-log replay has populated the historical event
   *  slots. The tool timeline pane uses this to switch empty-state copy. */
  replayed: boolean;
};

type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
  seedConversation: (entries: { user: string; agent: string; turn: number }[]) => void;
  seedFromSessionLog: (events: LogEventInput[]) => void;
  seedHypotheses: (rows: HypothesisRow[]) => void;
};

const _initialState = (): SessionState => ({
  status: "idle",
  messages: [],
  pendingAssistantText: "",
  toolCalls: [],
  thinkingEntries: [],
  dispatchEntries: [],
  hypotheses: new Map(),
  findings: [],
  budget: null,
  connBreakerTripped: false,
  log: [],
  replayed: false,
});

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),

    appendUserMessage: (text) =>
      set((s) => ({ messages: [...s.messages, { role: "user", text }] })),

    reset: () => set(_initialState()),

    seedConversation: (entries) =>
      set(() => {
        const messages: ChatMessage[] = [];
        for (const e of entries) {
          if (e.user) messages.push({ role: "user", text: e.user, turn: e.turn });
          if (e.agent) messages.push({ role: "assistant", text: e.agent, turn: e.turn });
        }
        return { messages };
      }),

    seedFromSessionLog: (events) =>
      set(() => {
        const toolCalls: ToolCall[] = [];
        const thinkingEntries: ThinkingEntry[] = [];
        const dispatchEntries: DispatchEntry[] = [];

        for (const e of events) {
          if (e.kind === "tool_call") {
            toolCalls.push({
              id: `${e.name}-${e.ts ?? ""}-${toolCalls.length}`,
              name: e.name,
              args: e.input,
              startedAt: 0,
            });
          } else if (e.kind === "tool_result") {
            for (let i = toolCalls.length - 1; i >= 0; i--) {
              if (!toolCalls[i].result) {
                toolCalls[i] = {
                  ...toolCalls[i],
                  result: { ok: e.ok, preview: e.preview },
                };
                break;
              }
            }
          } else if (e.kind === "thinking") {
            thinkingEntries.push({ text: e.content, ts: e.ts });
          } else if (e.kind === "dispatch") {
            dispatchEntries.push({
              specialty: e.specialty,
              phase: e.phase,
              content: e.content,
              ts: e.ts,
            });
          }
        }

        return { toolCalls, thinkingEntries, dispatchEntries, replayed: true };
      }),

    seedHypotheses: (rows) =>
      set(() => {
        const m = new Map<number, HypothesisRow>();
        for (const r of rows) m.set(r.id, r);
        return { hypotheses: m };
      }),

    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text":
            return { pendingAssistantText: s.pendingAssistantText + frame.delta };
          case "tool_call":
            return {
              toolCalls: [
                ...s.toolCalls,
                {
                  id: `${frame.name}-${Date.now()}-${s.toolCalls.length}`,
                  name: frame.name, args: frame.args, startedAt: Date.now(),
                },
              ],
            };
          case "tool_result": {
            const tc = [...s.toolCalls];
            for (let i = tc.length - 1; i >= 0; i--) {
              if (!tc[i].result) {
                tc[i] = { ...tc[i], result: { ok: frame.ok, preview: frame.preview } };
                break;
              }
            }
            return { toolCalls: tc };
          }
          case "thinking":
            return {
              thinkingEntries: [
                ...s.thinkingEntries,
                { text: frame.delta },
              ],
            };
          case "dispatch":
            return {
              dispatchEntries: [
                ...s.dispatchEntries,
                {
                  specialty: frame.specialist,
                  phase: frame.phase,
                  content: frame.child_session_id,
                },
              ],
            };
          case "finding":
            return { findings: [...s.findings, frame.row] };
          case "hypothesis": {
            const m = new Map(s.hypotheses);
            m.set(frame.row.id, frame.row);
            return { hypotheses: m };
          }
          case "budget":
            return { budget: { spent: frame.spent, remaining: frame.remaining, turn: frame.turn } };
          case "conn_breaker":
            return { connBreakerTripped: frame.tripped };
          case "log":
            return { log: [...s.log.slice(-499), { level: frame.level, msg: frame.msg, ts: Date.now() }] };
          case "status": {
            const next: Partial<SessionState> = { status: frame.phase as SessionState["status"] };
            if ((frame.phase === "awaiting_input" || frame.phase === "stopped" || frame.phase === "completed")
                && s.pendingAssistantText) {
              next.messages = [
                ...s.messages,
                { role: "assistant", text: s.pendingAssistantText, turn: frame.turns },
              ];
              next.pendingAssistantText = "";
            }
            return next;
          }
          case "kb_update":
            return {};
        }
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
