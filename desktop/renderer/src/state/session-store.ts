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
  | { type: "hypothesis"; action: string; row: unknown }
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

export type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  messages: ChatMessage[];
  pendingAssistantText: string;
  toolCalls: ToolCall[];
  hypotheses: unknown[];
  findings: unknown[];
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
};

type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
};

const _initialState = (): SessionState => ({
  status: "idle",
  messages: [],
  pendingAssistantText: "",
  toolCalls: [],
  hypotheses: [],
  findings: [],
  budget: null,
  connBreakerTripped: false,
  log: [],
});

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),
    appendUserMessage: (text) =>
      set((s) => ({ messages: [...s.messages, { role: "user", text }] })),
    reset: () => set(_initialState()),
    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text":
            return { pendingAssistantText: s.pendingAssistantText + frame.delta };
          case "tool_call":
            return {
              toolCalls: [
                ...s.toolCalls,
                { id: `${frame.name}-${Date.now()}-${s.toolCalls.length}`,
                  name: frame.name, args: frame.args, startedAt: Date.now() },
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
          case "finding":
            return { findings: [...s.findings, frame.row] };
          case "hypothesis":
            return { hypotheses: [...s.hypotheses, frame.row] };
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
          case "dispatch":
          case "thinking":
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
