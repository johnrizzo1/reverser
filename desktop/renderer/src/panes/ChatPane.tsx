import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";

export function ChatPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const messages = useStore(store, (s) => s.messages);
  const pending = useStore(store, (s) => s.pendingAssistantText);
  const status = useStore(store, (s) => s.status);
  const send = useSendMessage(sessionId);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pending]);

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
        {messages.length === 0 && !pending && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={
            m.role === "user"
              ? "max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap"
              : "max-w-[85%] text-neutral-200 text-sm whitespace-pre-wrap"
          }>
            {m.text}
          </div>
        ))}
        {pending && (
          <div className="max-w-[85%] text-neutral-300 text-sm whitespace-pre-wrap italic">
            {pending}
          </div>
        )}
      </div>
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
    </div>
  );
}
