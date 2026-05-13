import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";

type Entry =
  | { kind: "user"; text: string; turn?: number; idx: number }
  | { kind: "assistant"; text: string; turn?: number; idx: number }
  | { kind: "thinking_group"; turn: number; texts: string[]; idx: number }
  | { kind: "dispatch"; specialty: string; phase: string; content: string; turn?: number; idx: number };

const DISPATCH_PHASE_COLOR: Record<string, string> = {
  text: "text-neutral-300",
  tool_call: "text-cyan-400",
  tool_result: "text-green-400/80",
  tool_error: "text-red-400/80",
  error: "text-red-400/80",
  thinking: "text-neutral-500 italic",
  start: "text-neutral-400",
  result: "text-neutral-400",
};

export function ChatPane({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
}) {
  const store = getSessionStore(sessionId);
  const messages = useStore(store, (s) => s.messages);
  const pending = useStore(store, (s) => s.pendingAssistantText);
  const thinking = useStore(store, (s) => s.thinkingEntries);
  const dispatches = useStore(store, (s) => s.dispatchEntries);
  const status = useStore(store, (s) => s.status);
  const send = useSendMessage(sessionId);
  const [input, setInput] = useState("");
  const [expandedThinking, setExpandedThinking] = useState<Set<number>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  const entries = useMemo<Entry[]>(() => {
    let idx = 0;
    const out: Entry[] = [];

    for (const m of messages) {
      out.push({ kind: m.role, text: m.text, turn: m.turn, idx: idx++ });
    }
    for (const d of dispatches) {
      out.push({
        kind: "dispatch",
        specialty: d.specialty, phase: d.phase, content: d.content,
        turn: d.turn, idx: idx++,
      });
    }
    // Group thinking entries by turn (one collapsed row per turn).
    const byTurn = new Map<number, string[]>();
    for (const t of thinking) {
      const k = t.turn ?? -1;
      const arr = byTurn.get(k) ?? [];
      arr.push(t.text);
      byTurn.set(k, arr);
    }
    for (const [turn, texts] of byTurn) {
      out.push({ kind: "thinking_group", turn, texts, idx: idx++ });
    }

    // Sort by (turn asc, idx asc). Entries without a turn go to the end.
    return out.slice().sort((a, b) => {
      const at = a.turn ?? Number.POSITIVE_INFINITY;
      const bt = b.turn ?? Number.POSITIVE_INFINITY;
      if (at !== bt) return at - bt;
      return a.idx - b.idx;
    });
  }, [messages, dispatches, thinking]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [entries.length, pending]);

  const submit = async () => {
    if (!input.trim() || send.isPending) return;
    store.getState().appendUserMessage(input);
    const text = input;
    setInput("");
    await send.mutateAsync(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-auto p-4 space-y-3">
        {entries.length === 0 && !pending && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}

        {entries.map((e) => {
          if (e.kind === "user") {
            return (
              <div key={e.idx} className="max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap">
                {e.text}
              </div>
            );
          }
          if (e.kind === "assistant") {
            return (
              <div key={e.idx} className="max-w-[85%] text-neutral-200 text-sm whitespace-pre-wrap">
                {e.text}
              </div>
            );
          }
          if (e.kind === "dispatch") {
            const color = DISPATCH_PHASE_COLOR[e.phase] ?? "text-neutral-300";
            return (
              <div key={e.idx} className="text-xs font-mono">
                <span className="text-fuchsia-400">[{e.specialty}]</span>{" "}
                <span className={color}>{e.content}</span>
              </div>
            );
          }
          // thinking_group
          const expanded = expandedThinking.has(e.turn);
          return (
            <div key={e.idx} className="text-xs">
              <button
                onClick={() => {
                  const next = new Set(expandedThinking);
                  if (expanded) next.delete(e.turn); else next.add(e.turn);
                  setExpandedThinking(next);
                }}
                className="text-neutral-500 hover:text-neutral-300"
              >
                {expanded ? "▾" : "▸"} thinking · turn {e.turn} [{expanded ? "hide" : `show ${e.texts.length}`}]
              </button>
              {expanded && (
                <div className="mt-1 pl-4 space-y-1 italic text-neutral-500">
                  {e.texts.map((t, i) => <div key={i}>{t}</div>)}
                </div>
              )}
            </div>
          );
        })}

        {pending && (
          <div className="max-w-[85%] text-neutral-300 text-sm whitespace-pre-wrap italic">
            {pending}
          </div>
        )}
      </div>

      {!readOnly && (
        <div className="border-t border-neutral-800 p-2 flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder="type a message — ⌘/Ctrl+Enter to send"
          />
          <Button onClick={submit} disabled={!input.trim() || send.isPending || status === "running"}>
            Send
          </Button>
        </div>
      )}
    </div>
  );
}
