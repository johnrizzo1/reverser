import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";
import { TurnBubble } from "./TurnBubble";
import { UserBubble } from "./UserBubble";

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
  const send = useSendMessage(sessionId);
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
    if (!input.trim() || send.isPending) return;
    store.getState().appendUserMessage(input);
    const text = input;
    setInput("");
    await send.mutateAsync(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-auto p-4 space-y-3"
      >
        {sortedTurns.length === 0 && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}
        {sortedTurns.map((t) => (
          <div key={t.turn} className="space-y-2">
            {t.userMessage && <UserBubble text={t.userMessage} />}
            <TurnBubble turn={t} />
          </div>
        ))}
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
          <Button
            onClick={submit}
            disabled={!input.trim() || send.isPending || status === "running"}
          >
            Send
          </Button>
        </div>
      )}
    </div>
  );
}
