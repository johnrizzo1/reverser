import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useDeletePendingMessage, useQueuePendingMessage, useSendMessage } from "@/api/queries";
import { TurnBubble } from "./TurnBubble";
import { UserBubble } from "./UserBubble";
import { PendingMessageBubble } from "./PendingMessageBubble";
import { MessageSquareText, SendHorizontal } from "lucide-react";

export function ChatPane({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
}) {
  const store = getSessionStore(sessionId);
  const turns = useStore(store, (s) => s.turns);
  const status = useStore(store, (s) => s.status);
  const pendingMessages = useStore(store, (s) => s.pendingMessages);
  const send = useSendMessage(sessionId);
  const queuePending = useQueuePendingMessage(sessionId);
  const deletePending = useDeletePendingMessage(sessionId);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const sortedTurns = useMemo(
    () => Array.from(turns.values()).sort((a, b) => a.turn - b.turn),
    [turns],
  );

  const [nearBottom, setNearBottom] = useState(true);
  useEffect(() => {
    if (nearBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }
  }, [sortedTurns, nearBottom]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    setNearBottom(dist < 100);
  };

  const submit = async () => {
    const isRunning = status === "running";
    if (!input.trim() || queuePending.isPending || (!isRunning && send.isPending)) return;
    const text = input;
    setInput("");
    if (isRunning) {
      await queuePending.mutateAsync(text);
      return;
    }
    store.getState().appendUserMessage(text);
    await send.mutateAsync(text);
  };

  const deleteQueuedMessage = async (messageId: string) => {
    await deletePending.mutateAsync(messageId);
  };

  return (
    <div className="flex h-full flex-col">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-auto bg-[linear-gradient(180deg,rgba(255,255,255,0.018),transparent_120px)] p-4"
      >
        {sortedTurns.length === 0 && (
          <div className="flex h-full min-h-[18rem] items-center justify-center">
            <div className="max-w-sm rounded-md border border-neutral-800 bg-neutral-900/45 px-4 py-3 text-center shadow-sm">
              <MessageSquareText className="mx-auto mb-2 h-5 w-5 text-cyan-300/80" />
              <p className="text-sm font-medium text-neutral-200">No turn activity yet</p>
              <p className="mt-1 text-xs leading-5 text-neutral-500">
                Start with the current objective, target, or next analyst task.
              </p>
            </div>
          </div>
        )}
        <div className="space-y-4">
          {sortedTurns.map((t) => (
            <div key={t.turn} className="space-y-2">
              {t.userMessage && <UserBubble text={t.userMessage} />}
              <TurnBubble turn={t} />
            </div>
          ))}
          {pendingMessages.map((message) => (
            <PendingMessageBubble
              key={message.id}
              message={message}
              deleting={deletePending.isPending}
              onDelete={deleteQueuedMessage}
            />
          ))}
        </div>
      </div>

      {!readOnly && (
        <div className="border-t border-neutral-800 bg-neutral-950/80 p-2.5">
          <div className="flex items-end gap-2 rounded-md border border-neutral-800 bg-neutral-900/45 p-2 shadow-sm">
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
              className="border-neutral-800 bg-neutral-950/80 focus-visible:ring-cyan-500/40"
              placeholder="type a message — ⌘/Ctrl+Enter to send"
            />
            <Button
              onClick={submit}
              disabled={
                !input.trim()
                || queuePending.isPending
                || (status !== "running" && send.isPending)
              }
              className="h-9 gap-1.5"
              title={status === "running" ? "Queue for next turn" : "Send"}
            >
              <SendHorizontal className="h-3.5 w-3.5" />
              {status === "running" ? "Queue" : "Send"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
